import copy
import json
import re
from collections import defaultdict
import sys

from volttron.haystack.parser.driver.config_base import \
    DriverConfigGenerator


class JsonDriverConfigGenerator(DriverConfigGenerator):
    """
    Class that parses haystack tags from two json files - one containing tags
    for equipments/devices and another containing haystack tags for points
    This is a reference implementation to show case driver config generation
    based on haystack tags. This class can be extended and customized for
    specific device types and configurations
    For example, override self.driver_config_template and
    self.generate_config_from_template() if you want to generate
    driver configurations for bacnet or modbus drivers
    """

    def __init__(self, config):
        super().__init__(config)
        # get details on haystack metadata
        metadata = self.config_dict.get("metadata")
        try:
            with open(metadata.get("equip_json"), "r") as f:
                self.equip_json = json.load(f)
            with open(metadata.get("points_json"), "r") as f:
                self.points_json = json.load(f)
        except Exception:
            raise
        # Initialize map of haystack id and nf device name
        self.equip_id_device_name_map = dict()
        self.equip_id_device_id_map = dict()
        self.ahu_name_pattern = re.compile(r"\[\d+\]")
        self.equip_id_topic_name_map = dict()
        self.ahu_dict = None

    def _populate_equip_details(self):
        rows = self.equip_json['rows']
        self.ahu_dict = defaultdict(list)
        for _d in rows:

            # Check for power meter
            if self.configured_power_meter_id:
                if _d['id'] == self.configured_power_meter_id:
                    if self.power_meter_id is None:
                        self.power_meter_id = _d['id']
                    else:
                        raise ValueError(
                            f"More than one equipment found with the id {self.configured_power_meter_id}. Please "
                            f"add 'power_meter_id' parameter to configuration to uniquely identify whole "
                            f"building power meter")
            else:
                if self.power_meter_tag in _d:  # if tagged as whole building power meter
                    if self.power_meter_id is None:
                        self.power_meter_id = _d['id']
                    else:
                        raise ValueError(f"More than one equipment found with the tag {self.power_meter_tag}. Please "
                                         f"add 'power_meter_id' parameter to configuration to uniquely identify whole "
                                         f"building power meter")

            # check for ahu and vav
            id_list = _d["id"].split(".")
            device = id_list[-1] if id_list else ""
            if "vav" in _d:  # if it tagged as vav
                ahu_id = _d.get("ahuRef")
                if ahu_id:
                    self.ahu_dict[ahu_id].append(_d["id"])
                else:
                    # add to the list of unmapped
                    self.ahu_dict[""].append(_d["id"])
                    self.unmapped_device_details[_d["id"]] = {"type": "vav", "error": "Unable to find ahuRef"}
                self.vav_list.append(_d["id"])
            elif "ahu" in _d:  # if it is tagged as ahu
                self.ahu_list.append(_d["id"])
        if not set(self.ahu_list).issubset(set(self.ahu_dict)):
            for a in set(self.ahu_list) - set(self.ahu_dict):
                self.ahu_dict[a] = []

    def get_ahu_and_vavs(self):
        if not self.ahu_dict:
            self._populate_equip_details()
        return self.ahu_dict

    def get_building_meter(self):
        if not self.power_meter_id:
            self._populate_equip_details()
        if self.power_meter_id:
            return self.power_meter_id
        else:
            raise ValueError(f"Unable to find building power meter using power_meter_tag {self.power_meter_tag} or "
                             f"configured power_meter_id {self.configured_power_meter_id}")

    def get_nf_device_id_and_name(self, equip_id, equip_type="vav"):

        if not self.equip_id_device_name_map:
            # Load it once and use it from map from next call
            rows = self.points_json['rows']
            for _d in rows:
                id_list = _d["id"].split(".")
                device = id_list[-2] if id_list else ""
                if _d["equipRef"] in self.vav_list:
                    self.equip_id_topic_name_map[_d["equipRef"]] = _d["topic_name"]
                    self.equip_id_device_name_map[_d["equipRef"]] = \
                        self.get_object_name_from_topic(_d["topic_name"],
                                                        "vav")
                    self.equip_id_device_id_map[_d["equipRef"]] = _d["topic_name"].split("/")[4]
                    if _d["equipRef"] in self.unmapped_device_details:
                        # grab the topic_name to shed some light into ahu mapping
                        self.unmapped_device_details[_d["equipRef"]]["topic_name"] = _d["topic_name"]
                elif _d["equipRef"] in self.ahu_list or _d["equipRef"] == self.power_meter_id:
                    self.equip_id_topic_name_map[_d["equipRef"]] = _d["topic_name"]
                    equip_type = "meter" if _d["equipRef"] == self.power_meter_id else "ahu"
                    try:
                        self.equip_id_device_name_map[_d["equipRef"]] = \
                            self.get_object_name_from_topic(_d["topic_name"],
                                                            equip_type)
                        self.equip_id_device_id_map[_d["equipRef"]] = \
                            _d["topic_name"].split("/")[4]
                    except ValueError:
                        # ignore as some points might not follow the pattern
                        # for topic name. As long as we find a single point
                        # with topic name matching the pattern we will use that
                        print(f"Ignoring point {_d['topic_name']}")
                        continue


        return self.equip_id_device_id_map.get(equip_id), self.equip_id_device_name_map.get(equip_id)

    # To be overridden by subclass if there is custom site specific parsing logic
    def get_object_name_from_topic(self, topic_name, equip_type):
        # need device name only if device id is not unique
        if "attr_prop_object_name" in \
                self.config_template["driver_config"]["query"]:
            if equip_type == "ahu":
                part = topic_name.split("/")[-1]
                m = re.search(self.ahu_name_pattern, part)
                if m is None:
                    raise ValueError(
                        f"Unable to ahu object name from {topic_name} "
                        f"using pattern {self.ahu_name_pattern}")
                match = m.group(0)
                return match.replace("[", "(").replace("]", ")")
            elif equip_type == "vav":
                return topic_name.split("/")[-1].split(":")[0]
            else:
                return topic_name.split("/")[-1]
        return ""

    def generate_config_from_template(self, equip_id, equip_type):
        device_id, device_name = self.get_nf_device_id_and_name(equip_id,
                                                                equip_type)
        driver = copy.deepcopy(self.config_template)
        nf_query_format = driver["driver_config"]["query"]
        if "{device_id}" in nf_query_format and device_id is None or \
            "{obj_name}" in nf_query_format and device_name is None:
            if not self.unmapped_device_details.get(equip_id):
                self.unmapped_device_details[equip_id] = dict()
            self.unmapped_device_details[equip_id]["type"] = equip_type
            self.unmapped_device_details[equip_id]["topic_name"] = self.equip_id_topic_name_map[equip_id]
            self.unmapped_device_details[equip_id]["error"] = "Unable to parse any of the point topic names for " \
                                                              "nf device id and/or nf object name"
            return None
        else:
            nf_query = nf_query_format.format(device_id=device_id,
                                              obj_name=device_name)
            driver["driver_config"]["query"] = nf_query
            return driver

    def get_name_from_id(self, id):
        name = None
        if id:
            name = id.split(".")[-1]
        return name


def main():
    if len(sys.argv) != 2:
        print("script requires one argument - path to configuration file")
        exit()
    config_path = sys.argv[1]
    d = JsonDriverConfigGenerator(config_path)
    d.generate_configs()


if __name__ == '__main__':
    main()

