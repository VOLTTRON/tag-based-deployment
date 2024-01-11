import json
import os.path
import sys
from abc import abstractmethod
import copy
import shutil
from volttron.haystack.parser.utils import strip_comments
from volttron.haystack.parser.ilc.utils.validate_pairwise import extract_criteria as pairwise_extract_criteria, \
    validate_input as pairwise_validate_input, calc_column_sums as pairwise_calc_column_sums


class ILCConfigGenerator:
    """
    Base class that parses haystack tags to generate
    ILC agent configurations based on a configuration templates
    """

    def __init__(self, config):
        if isinstance(config, dict):
            self.config_dict = config
        else:
            try:
                with open(config, "r") as f:
                    self.config_dict = json.loads(strip_comments(f.read()))
            except Exception:
                raise

        self.site_id = self.config_dict.get("site_id", "")
        self.building = self.config_dict.get("building", "")
        self.campus = self.config_dict.get("campus", "")
        if not self.building and self.site_id:
            self.building = self.get_name_from_id(self.site_id)
        if not self.campus and self.site_id:
            self.campus = self.site_id.split(".")[-2]

        self.topic_prefix = self.campus + "/" if self.campus else ""
        self.topic_prefix = self.topic_prefix + self.building + "/" if self.building else ""
        self.power_meter_tag = 'siteMeter'
        self.power_meter_name = self.config_dict.get("building_power_meter", "")
        self.building_power_point = self.config_dict.get("building_power_point", "")
        self.configured_power_meter_id = self.config_dict.get("power_meter_id", "")

        self.power_meter_id = None

        self.point_meta_map = self.config_dict.get("point_meta_map")
        self.point_meta_field = self.config_dict.get("point_meta_field",
                                                     "miniDis")
        self.building_power_point_type = self.point_meta_map["WholeBuildingPower"]

        self.volttron_point_types_vav = [x for x in self.point_meta_map if x != "WholeBuildingPower"]
        self.point_types_vav = [self.point_meta_map[x] for x in self.volttron_point_types_vav]
        # Initialize point mapping for ilc config
        self.point_mapping = {x: [] for x in self.point_meta_map.keys()}

        # use this dict to give additional details for user to help manually find the issue
        self.unmapped_device_details = dict()
        # For all unmapped devices add topic name details to this variable for error reporting
        self.equip_id_point_topic_map = dict()

        config_template = self.config_dict.get("config_template")
        if not config_template:
            raise ValueError(f"Missing parameter in config:'config_template'")

        self.device_type = config_template.get("device_type")
        if not self.device_type:
            raise ValueError("Missing device_type parameter under config_template")

        self.pairwise_path = os.path.join(os.path.dirname(__file__),
                                          f"pairwise_criteria_{self.device_type}.json")
        if not os.path.exists(self.pairwise_path):
            raise ValueError(f"Given device type is {self.device_type}. But unable to find corresponding "
                             f"pairwise criteria file {self.pairwise_path}")

        self.ilc_template = {
            "campus": self.campus,
            "building": self.building,
            "power_meter": {
                "device_topic": "",
                "point": ""
            },
            "application_category": "Load Control",
            "application_name": "Intelligent Load Control",
            "clusters": [
                {
                    "device_control_config": f"config://{self.device_type}_control.config",
                    "device_criteria_config": f"config://{self.device_type}_criteria.config",
                    "pairwise_criteria_config": f"config://{self.device_type}_criteria_matrix.json",
                    "cluster_priority": 1.0
                }
            ]
        }
        self.ilc_template.update(config_template["ilc_config"])

        self.control_template = config_template["control_config"]

        self.criteria_template = {"device_topic": ""}
        self.criteria_template.update(config_template["criteria_config"])
        self.mappers = config_template.get('mapper_config', {})

        # initialize output dir
        default_prefix = self.building + "_" if self.building else ""
        self.output_dir = self.config_dict.get(
            "output_dir", f"{default_prefix}ILC_configs")
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir, exist_ok=True)
        elif not os.path.isdir(self.output_dir):
            raise ValueError(f"Output directory {self.output_dir} "
                             f"does not exist")
        print(f"Output directory {os.path.abspath(self.output_dir)}")

        self.output_configs = os.path.join(self.output_dir, "configs")
        os.makedirs(self.output_configs, exist_ok=True)
        self.output_errors = os.path.join(self.output_dir, "errors")
        os.makedirs(self.output_errors, exist_ok=True)

        self.ilc_agent_vip = self.config_dict.get("ilc_agent_vip", "platform.ilc")

        # Initialize map of haystack id and nf device name
        self.equip_id_point_map = dict()
        self.equip_id_device_id_map = dict()
        self.config_metadata_dict = dict()
        self.config_metadata_dict[self.ilc_agent_vip] = []

    def generate_configs(self):
        """
        Generated all configuration files for ILC agent for a given site
        """

        self.generate_pairwise_config()

        self.generate_ilc_config()

        # Generated ilc config. Now generated config with vav details
        self.generate_control_config()
        self.generate_criteria_config()

        if self.config_metadata_dict[self.ilc_agent_vip]:
            config_metafile_name = f"{self.output_dir}/config_metadata.json"
            with open(config_metafile_name, 'w') as f:
                json.dump(self.config_metadata_dict, f, indent=4)

        if self.unmapped_device_details:
            err_file_name = f"{self.output_errors}/unmapped_device_details"
            with open(err_file_name, 'w') as outfile:
                json.dump(self.unmapped_device_details, outfile, indent=4)

            sys.stderr.write(f"\nUnable to generate configurations for all AHUs and VAVs. "
                             f"Please see {err_file_name} for details\n")
            sys.exit(1)
        else:
            sys.exit(0)

    def generate_pairwise_config(self):

        # Validate pairwise criteria if needed. exit if validation fails
        if self.config_dict.get("config_template").get("validate_pairwise_criteria"):
            pairwise_dict = None
            try:
                with open(self.pairwise_path, "r") as f:
                    pairwise_dict = json.loads(strip_comments(f.read()))
            except Exception as e:
                raise ValueError(f"Invalid json: pairwise criteria file {self.pairwise_path} failed. Exception {e}")

            criteria_labels, criteria_array = pairwise_extract_criteria(pairwise_dict["curtail"])
            col_sums = pairwise_calc_column_sums(criteria_array)

            result, ratio = pairwise_validate_input(criteria_array, col_sums)
            if not result:
                sys.stderr.write(f"\nValidation of pairwise criteria file {self.pairwise_path} failed.\n"
                                 f"Computed criteria array:{criteria_array} column sums:{col_sums}\n"
                                 f"Inconsistency ratio is: {ratio}\n")
                sys.exit(1)

        # write pairwise criteria file
        file_name = f"{self.device_type}_criteria_matrix.json"
        file_path = os.path.abspath(os.path.join(self.output_configs, file_name))
        shutil.copy(self.pairwise_path, file_path)
        self.config_metadata_dict[self.ilc_agent_vip].append({"config-name": file_name, "config": file_path})

    def generate_ilc_config(self):
        if not self.power_meter_name or not self.building_power_point:
            # if both are provided then use that don't try to look up device or point.
            # it might be a power meter agent and no actual power meter device
            try:
                self.power_meter_id = self.get_building_power_meter()
                if not self.power_meter_id:
                    self.unmapped_device_details["building_power_meter"] = {
                        "type": "building power meter",
                        "error": "Unable to locate building power meter"}
            except ValueError as e:
                # more than one power meter found either using siteMeter tag or with configured power meter id
                self.unmapped_device_details["building_power_meter"] = {
                    "type": "building power meter",
                    "error": f"Unable to locate building power meter: Error: {e}"}

            if not self.building_power_point:
                self.building_power_point = self.get_building_power_point()

            if not self.building_power_point and \
                    self.power_meter_id and \
                    not self.unmapped_device_details.get(self.power_meter_id):
                self.unmapped_device_details[self.power_meter_id] = {
                    "type": "building power meter",
                    "error": f"Unable to locate building power point using the metadata "
                             f"{self.building_power_point_type}"}

            if not self.power_meter_name and self.power_meter_id:
                self.power_meter_name = self.get_name_from_id(self.power_meter_id)

            #  Error case
            if not self.power_meter_name and not self.unmapped_device_details["building_power_meter"]:
                err = f"Unable to locate building power meter using the tag '{self.power_meter_tag}' "
                if self.configured_power_meter_id:
                    err = f"Unable to locate building power meter using id '{self.configured_power_meter_id}' "

                self.unmapped_device_details["building_power_meter"] = {
                    "type": "building power meter",
                    "error": err
                }

        # Generate ilc config file and metadata
        self.ilc_template["power_meter"]["device_topic"] = self.topic_prefix + self.power_meter_name
        self.ilc_template["power_meter"]["point"] = self.building_power_point
        file_path = os.path.abspath(os.path.join(self.output_configs, "ilc.config"))
        with open(file_path, 'w') as outfile:
            json.dump(self.ilc_template, outfile, indent=4)

        self.config_metadata_dict[self.ilc_agent_vip].append({"config-name": "config", "config": file_path})

    def generate_control_config(self):
        control_config = dict()

        vav_details = self.get_vavs_with_ahuref()
        if isinstance(vav_details, dict):
            iterator = vav_details.items()
        else:
            iterator = vav_details

        for vav_id, ahu_id in iterator:
            skip_vav = False
            config = copy.deepcopy(self.control_template)
            vav = self.get_name_from_id(vav_id)
            if ahu_id:
                vav_topic = self.get_name_from_id(ahu_id) + "/" + vav
            else:
                vav_topic = vav
            config["device_topic"] = self.topic_prefix + vav_topic
            point_mapping = dict()
            # get vav point name
            for volttron_point_type in self.volttron_point_types_vav:
                point_name = self.get_point_name(vav_id, "vav", volttron_point_type)
                if point_name:
                    point_mapping[volttron_point_type] = point_name
                else:
                    if not self.unmapped_device_details.get(vav_id):
                        self.unmapped_device_details[vav_id] = {
                           "type": "vav",
                           "error": f"Unable to find point of type {volttron_point_type} "
                                    f"using metadata field {self.point_meta_field} and "
                                    f"configured point mapping {self.point_meta_map[volttron_point_type]}"
                        }
                    skip_vav = True

            if skip_vav:
                # some points are missing, details in umapped_device_details skip vav and move to next
                continue

            # If all necessary points are found go ahead and add it to control config
            volttron_point = config["curtail_settings"]["point"]
            config["curtail_settings"]["point"] = point_mapping[volttron_point]

            # More than 1 curtail possible? should we loop through?
            volttron_point_list = config["device_status"]["curtail"]["device_status_args"]

            # sort the list of point before doing find and replace of volttron point name with actual point names
            # so that we avoid matching substrings. For example find and replace ZoneAirFlowSetpoint before ZoneAirFlow
            volttron_point_list.sort(key=len)

            v_conditions = config["device_status"]["curtail"]["condition"]

            for condition in v_conditions:
                for point in volttron_point_list:
                    condition = condition.replace(point, point_mapping[point])

            point_list = [point_mapping[point] for point in volttron_point_list]

            # replace curtail values with actual point names
            config["device_status"]["curtail"]["device_status_args"] = point_list
            config["device_status"]["curtail"]["condition"] = condition
            control_config[vav_topic] = {vav: config}

        if control_config:
            file_name = f"{self.device_type}_control.config"
            file_path = os.path.abspath(os.path.join(self.output_configs, file_name))
            with open(file_path, 'w') as outfile:
                json.dump(control_config, outfile, indent=4)
            self.config_metadata_dict[self.ilc_agent_vip].append({"config-name": file_name, "config": file_path})

    def generate_criteria_config(self):
        # sort the list of point before doing find and replace of volttron point name with actual point names
        # so that we avoid matching substrings. For example find and replace ZoneAirFlowSetpoint before ZoneAirFlow
        self.volttron_point_types_vav.sort(key=len)

        criteria_config = dict()

        vav_details = self.get_vavs_with_ahuref()
        if isinstance(vav_details, dict):
            iterator = vav_details.items()
        else:
            iterator = vav_details

        for vav_id, ahu_id in iterator:
            skip_vav = False
            curtail_config = {"device_topic": ""}
            curtail_config.update(copy.deepcopy(self.criteria_template))
            vav = self.get_name_from_id(vav_id)
            if ahu_id:
                vav_topic = self.get_name_from_id(ahu_id) + "/" + vav
            else:
                vav_topic = vav
            curtail_config["device_topic"] = self.topic_prefix + vav_topic
            point_mapping = dict()
            # get vav point name
            for volttron_point_type in self.volttron_point_types_vav:
                point_name = self.get_point_name(vav_id, "vav", volttron_point_type)
                if point_name:
                    point_mapping[volttron_point_type] = point_name
                else:
                    skip_vav = True
                    if not self.unmapped_device_details.get(vav_id):
                        self.unmapped_device_details[vav_id] = {
                           "type": "vav",
                           "error": f"Unable to find point of "
                                    f"type {volttron_point_type} using metadata field {self.point_meta_field} and "
                                    f"configured point mapping {self.point_meta_map[volttron_point_type]}"
                        }

            if skip_vav:
                # some points are missing, details in umapped_device_details skip vav and move to next
                continue

            for key, value_dict in curtail_config.items():
                if key in ["room_type", "device_topic"]:
                    continue
                # else it is an operation - look for operation and operation_args and replace
                # volttron point names with actual point names

                # Replace in "operation"
                value_dict["operation"] = self.replace_point_names(value_dict["operation"],
                                                                   point_mapping,
                                                                   self.volttron_point_types_vav)

                # Replace in "operation_args"
                if isinstance(value_dict["operation_args"], dict):
                    value_dict["operation_args"]["always"] = self.replace_point_names(
                        value_dict["operation_args"]["always"],
                        point_mapping,
                        self.volttron_point_types_vav)
                    value_dict["operation_args"]["nc"] = self.replace_point_names(
                        value_dict["operation_args"]["nc"],
                        point_mapping,
                        self.volttron_point_types_vav)
                else:
                    # it's a list
                    value_dict["operation_args"] = self.replace_point_names(
                        value_dict["operation_args"],
                        point_mapping,
                        self.volttron_point_types_vav)

            criteria_config[vav_topic] = {vav: curtail_config}

        if criteria_config:
            criteria_config['mappers'] = self.mappers
            file_name = f"{self.device_type}_criteria.config"
            file_path = os.path.abspath(os.path.join(self.output_configs, file_name))
            with open(file_path, 'w') as outfile:
                json.dump(criteria_config, outfile, indent=4)
            self.config_metadata_dict[self.ilc_agent_vip].append({"config-name": file_name, "config": file_path})

    @staticmethod
    def replace_point_names(search_obj, point_mapping, volttron_point_list):
        if isinstance(search_obj, str):
            for point in volttron_point_list:
                search_obj = search_obj.replace(point, point_mapping[point])
            return search_obj

        else:
            new_list = []
            for search_str in search_obj:
                for point in volttron_point_list:
                    search_str = search_str.replace(point, point_mapping[point])
                new_list.append(search_str)
            return new_list

    @abstractmethod
    def get_building_power_meter(self):
        pass

    @abstractmethod
    def get_building_power_point(self):
        pass

    @abstractmethod
    def get_point_name(self, equip_id, equip_type, point_key):
        pass
    
    @abstractmethod
    def get_name_from_id(self, id):
        pass

    @abstractmethod
    def get_vavs_with_ahuref(self):
        """
        Should return vavs with its corresponding ahu
        :return: list of tuples with the format [(va1, ahu1), (vav2,ahu1),...]
                 or dict mapping vav to ahu with format
                 {'vav1':'ahu1', 'vav2':'ahu1',...}
        """
        pass
