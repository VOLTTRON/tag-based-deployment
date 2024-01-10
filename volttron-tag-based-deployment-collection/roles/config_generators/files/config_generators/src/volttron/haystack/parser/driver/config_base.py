import json
import os.path
import sys
from abc import abstractmethod
from volttron.haystack.parser.utils import strip_comments


class DriverConfigGenerator:
    """
    Base class that parses haystack tags to generate
    platform driver configuration based on a configuration template
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

        topic_prefix = self.config_dict.get("topic_prefix")
        if not topic_prefix:
            topic_prefix = "devices"
            if self.campus:
                topic_prefix = topic_prefix + f"/{self.campus}"
            if self.building:
                topic_prefix = topic_prefix + f"/{self.building}"

        if not topic_prefix.endswith("/"):
            topic_prefix = topic_prefix + "/"
        self.ahu_topic_pattern = topic_prefix + "{}"
        self.meter_topic_pattern = topic_prefix + "{}"
        self.vav_topic_pattern = topic_prefix + "{ahu}/{vav}"

        # List of all ahus equip ids
        self.ahu_list = []
        # List of all vav equip ids
        self.vav_list = []

        self.power_meter_tag = 'siteMeter'
        self.configured_power_meter_id = self.config_dict.get("power_meter_id", "")
        self.power_meter_name = self.config_dict.get("building_power_meter", "")

        self.power_meter_id = None

        # If there are any vav's that are not mapped to a AHU use this dict to give additional details for user
        # to help manually find the corresponding ahu
        self.unmapped_device_details = dict()

        self.config_template = self.config_dict.get("config_template")

        # initialize output dir
        default_prefix = self.building + "_" if self.building else ""
        self.output_dir = self.config_dict.get(
            "output_dir", f"{default_prefix}driver_configs")
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
        self.driver_vip = self.config_dict.get("driver_vip", "platform.driver")

    @abstractmethod
    def get_ahu_and_vavs(self):
        """
        Should return a list of ahu and vav mappings
        :return: list of tuples with the format [(ahu1, (vav1,vav2..)),...]
                 or dict mapping ahu with vavs with format
                 {'ahu1':(vav1,vav2,..), ...}
        """
        pass

    @abstractmethod
    def get_building_meter(self):
        """
        Should return a meter.

        """
        pass

    def generate_configs(self):
        ahu_and_vavs = self.get_ahu_and_vavs()
        if isinstance(ahu_and_vavs, dict):
            iterator = ahu_and_vavs.items()
        else:
            iterator = ahu_and_vavs
        for ahu_id, vavs in iterator:
            ahu_name, result_dict = self.generate_ahu_configs(ahu_id, vavs)
            if not result_dict:
                continue  # no valid configs, move to the next ahu
            if ahu_name:
                with open(f"{self.output_configs}/{ahu_name}.json", 'w') as outfile:
                    json.dump(result_dict, outfile, indent=4)
            else:
                with open(f"{self.output_errors}/unmapped_vavs.json", 'w') as outfile:
                    json.dump(result_dict, outfile, indent=4)

        try:
            self.get_building_meter()
        except ValueError as e:
            return {"building_power_meter": {"error": f"Unable to locate building power meter: Error: {e}"}}

        meter_name, result_dict = self.generate_meter_config()
        with open(f"{self.output_configs}/{meter_name}.json", 'w') as outfile:
            json.dump(result_dict, outfile, indent=4)

        # If unmapped devices exists, write additional unmapped_devices.txt that gives more info to user to map manually
        if self.unmapped_device_details:
            err_file = f"{self.output_errors}/unmapped_device_details"
            with open(err_file, 'w') as outfile:
                json.dump(self.unmapped_device_details, outfile, indent=4)

            sys.stderr.write(f"\nUnable to generate configurations for all AHUs and VAVs. "
                             f"Please see {err_file} for details\n")
            sys.exit(1)
        else:
            sys.exit(0)

    def generate_meter_config(self):
        final_mapper = dict()
        final_mapper[self.driver_vip] = []
        meter = ""
        meter = self.get_name_from_id(self.power_meter_id)
        topic = self.meter_topic_pattern.format(meter)
        driver_config = self.generate_config_from_template(self.power_meter_id, 'meter')
        if driver_config:
            final_mapper[self.driver_vip].append({"config-name": topic, "config": driver_config})
        return meter, final_mapper

    def generate_ahu_configs(self, ahu_id, vavs):
        final_mapper = dict()
        final_mapper[self.driver_vip] = []
        ahu = ""
        ahu = self.get_name_from_id(ahu_id)
        # First create the config for the ahu
        topic = self.ahu_topic_pattern.format(ahu)
        if ahu_id:
            # replace right variables in driver_config_template
            driver_config = self.generate_config_from_template(ahu_id, "ahu")
            if driver_config:
                final_mapper[self.driver_vip].append({"config-name": topic, "config": driver_config})
            topic_pattern = self.vav_topic_pattern.format(ahu=ahu, vav='{vav}')  # fill ahu, leave vav variable
        else:
            topic_pattern = self.vav_topic_pattern.replace("{ahu}/", "")  # ahu
        # Now loop through and do the same for all vavs
        for vav_id in vavs:
            vav = self.get_name_from_id(vav_id)
            topic = topic_pattern.format(vav=vav)
            # replace right variables in driver_config_template
            driver_config = self.generate_config_from_template(vav_id, "vav")
            if driver_config:
                final_mapper[self.driver_vip].append({"config-name": topic, "config": driver_config})
        return ahu, final_mapper

    @abstractmethod
    def generate_config_from_template(self, equip_id, equip_type):
        pass

    @abstractmethod
    def get_name_from_id(self, id):
        pass
