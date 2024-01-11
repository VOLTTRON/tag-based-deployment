import json
import os.path
import sys
from abc import abstractmethod
from volttron.haystack.parser.utils import strip_comments
import copy

# TODO - get ILC details and pull AirsideRCxConfigGenerator and
#  into 1 base class DriverConfigGenerator
class AirsideRCxConfigGenerator:
    """
    Base class that parses haystack tags to generate
    AirsideRCx agent configuration based on a configuration template
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
        self.building = self.config_dict.get("building")
        self.campus = self.config_dict.get("campus")
        if not self.building and self.site_id:
            self.building = self.get_name_from_id(self.site_id)
        if not self.campus and self.site_id:
            self.campus = self.site_id.split(".")[-2]

        # List of all ahus equip ids
        self.ahu_list = []
        # List of all vav equip ids
        self.vav_list = []

        self.point_meta_map = self.config_dict.get("point_meta_map")
        self.point_meta_field = self.config_dict.get("point_meta_field",
                                                     "miniDis")
        self.point_default_map = self.config_dict.get("point_default_map", dict())

        # Initialize point mapping for airsidercx config
        self.point_mapping = {x: [] for x in self.point_meta_map.keys()}
        self.volttron_point_types_ahu = ["fan_status", "duct_stcpr", "duct_stcpr_stpt",
                                         "sa_temp", "sat_stpt", "fan_speedcmd"]
        self.volttron_point_types_vav = ["zone_reheat", "zone_damper"]
        self.ahu_point_types = [self.point_meta_map[x] for x in
                                self.volttron_point_types_ahu]
        self.ahu_mandatory_types = [self.point_meta_map["fan_status"],
                                    self.point_meta_map["fan_speedcmd"],
                                    self.point_meta_map["duct_stcpr"]]
        self.vav_point_types = [self.point_meta_map[x] for x in
                                self.volttron_point_types_vav]
        self.vav_mandatory_types = [self.point_meta_map["zone_damper"]]

        # If there are any vav's that are not mapped to a AHU or device which do not have the right point details
        # use this dict to give additional details for user to help manually find the issue
        self.unmapped_device_details = dict()
        # For all unmapped devices add topic name details to this variable for error reporting
        self.equip_id_point_topic_map = dict()

        self.config_template = self.config_dict.get("config_template")
        self.config_template["device"] = {
            "campus": self.campus,
            "building": self.building,
            "unit": {}
        }
        # initialize output dir
        default_prefix = self.building + "_" if self.building else ""
        self.output_dir = self.config_dict.get(
            "output_dir", f"{default_prefix}airsidercx_configs")
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

        self.agent_vip_prefix = self.config_dict.get("agent_vip_prefix", "airside")

        # Initialize map of haystack id and nf device name
        self.equip_id_point_map = dict()
        self.equip_id_device_id_map = dict()

    @abstractmethod
    def get_ahu_and_vavs(self):
        """
        Should return a list of ahu and vav mappings
        :return: list of tuples with the format [(ahu1, (vav1,vav2..)),...]
                 or dict mapping ahu with vavs with format
                 {'ahu1':(vav1,vav2,..), ...}
        """
        pass

    def generate_configs(self):
        config_metadata = dict()
        ahu_and_vavs = self.get_ahu_and_vavs()
        if isinstance(ahu_and_vavs, dict):
            iterator = ahu_and_vavs.items()
        else:
            iterator = ahu_and_vavs
        for ahu_id, vavs in iterator:
            ahu_name, result_dict = self.generate_ahu_configs(ahu_id, vavs)
            if not result_dict or not ahu_name:
                continue  # no valid configs or no valid ahu ref. move to the next ahu
            config_file_name = os.path.abspath(f"{self.output_configs}/{ahu_name}.json")
            with open(config_file_name, 'w') as f:
                json.dump(result_dict, f, indent=4)
            config_metadata[f'{self.agent_vip_prefix}.{ahu_name}'] = [{"config": config_file_name}]

        if config_metadata:
            config_metafile_name = f"{self.output_dir}/config_metadata.json"
            with open(config_metafile_name, 'w') as f:
                json.dump(config_metadata, f, indent=4)

        if self.unmapped_device_details:
            err_file_name = f"{self.output_errors}/unmapped_device_details"
            with open(err_file_name, 'w') as f:
                json.dump(self.unmapped_device_details, f, indent=4)

            sys.stderr.write(f"\nUnable to generate configurations for all AHUs and VAVs. "
                             f"Please see {err_file_name} for details\n")
            sys.exit(1)
        else:
            sys.exit(0)

    def generate_ahu_configs(self, ahu_id, vavs):
        if not ahu_id:
            return None, None
        final_config = copy.deepcopy(self.config_template)
        ahu = self.get_name_from_id(ahu_id)
        final_config["device"]["unit"] = {}
        final_config["device"]["unit"][ahu] = {}
        subdevices = final_config["device"]["unit"][ahu]["subdevices"] = list()
        point_mapping = final_config["arguments"]["point_mapping"]
        # Get ahu point details
        ahu_point_name_map = dict()
        for volttron_point_type in self.volttron_point_types_ahu:
            point_name = self.get_point_name(ahu_id, "ahu", volttron_point_type)
            if not point_name:
                # see if there is a default
                point_name = self.point_default_map.get(volttron_point_type, "")
            point_mapping[volttron_point_type] = point_name

        # varify if mandatory ahu points are there
        # fan_status or fan_speedcmd should be available for airsidercx
        if not point_mapping.get("fan_status") and not point_mapping.get("fan_speedcmd"):
            # Cannot proceed. Add detals to unmapped devices dict and return None
            self.unmapped_device_details[ahu_id] = {"type": "ahu",
                                                    "error": "Neither fan_status nor fan_speedcmd point is available",
                                                    "topic_name": self.equip_id_point_topic_map.get(ahu_id)}

            return ahu, None

        # check for warnings:
        if not point_mapping.get("duct_stcpr"):
            self.unmapped_device_details[ahu_id] = {"type": "ahu",
                                                    "error": "Warning. No point of type duct_stcpr was found",
                                                    "topic_name": self.equip_id_point_topic_map.get(ahu_id)}

        # Initialize vav point mapping to set as there can be more than 1
        for volttron_point_type in self.volttron_point_types_vav:
            point_mapping[volttron_point_type] = set()

        # Now loop through and populate vav details
        for vav_id in vavs:
            vav = self.get_name_from_id(vav_id)
            subdevices.append(vav)
            # get vav point name
            for volttron_point_type in self.volttron_point_types_vav:
                point_name = self.get_point_name(vav_id, "vav", volttron_point_type)
                if not point_name:
                    # see if there is a default
                    point_name = self.point_default_map.get(volttron_point_type, "")
                if point_name:
                    point_mapping[volttron_point_type].add(point_name)

        # convert set to list before returning i.e. written to file
        for volttron_point_type in self.volttron_point_types_vav:
            if not point_mapping[volttron_point_type]:
                point_mapping[volttron_point_type] = ""

                if volttron_point_type == "zone_damper":
                    # Add warning message with topic names of vavs
                    self.unmapped_device_details[ahu_id] = {"type": "vav",
                                                            "error": "Warning. No point of type zone_damper was found",
                                                            "topic_name": dict()}
                    for vav_id in vavs:
                        if vav_id in self.equip_id_point_topic_map:
                            # max two(one for each interested point) topic names for each vav available
                            self.unmapped_device_details[ahu_id]["topic_name"][vav_id] = \
                                self.equip_id_point_topic_map[vav_id]

            elif len(point_mapping[volttron_point_type]) > 1:
                point_mapping[volttron_point_type] = list(point_mapping[volttron_point_type])
            else:
                point_mapping[volttron_point_type] = point_mapping[volttron_point_type].pop()

        return ahu, final_config

    @abstractmethod
    def get_point_name(self, equip_id, equip_type, point_key):
        pass
    
    @abstractmethod
    def get_name_from_id(self, id):
        pass

