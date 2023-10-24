import copy
import sys
import re

import psycopg2
from volttron.haystack.parser.driver.config_base import \
    DriverConfigGenerator


class IntellimationDriverConfigGenerator(DriverConfigGenerator):
    """
    class that parses haystack tags from a postgres db to generate
    platform driver configuration for normal framework driver type
    """
    def __init__(self, config):
        super().__init__(config)

        # get details on haystack metadata
        metadata = self.config_dict.get("metadata")
        connect_params = metadata.get("connection_params")
        if "timescale_dialect" in metadata:
            self.timescale_dialect = metadata.get("timescale_dialect",
                                                  False)
            del connect_params["timescale_dialect"]
        else:
            self.timescale_dialect = False
        self.connection = psycopg2.connect(**connect_params)
        self.connection.autocommit = True
        self.equip_table = metadata.get("equip_table")
        self.point_table = metadata.get("point_table")
        self.ahu_name_pattern = re.compile(r"\[\d+\]")
        self.equip_id_topic_name_map = dict()

    def get_ahu_and_vavs(self):

        # 1. Query for vavs that are mapped to ahu
        query = f"SELECT tags #>>'{{ahuRef}}', json_agg(topic_name) \
        FROM {self.equip_table} \
        WHERE tags->>'ahuRef' is NOT NULL AND tags->>'ahuRef' != '' AND tags->>'vav'='m:' "
        print(f"site id is {self.site_id}")
        if self.site_id:
            query = query + f" AND tags->>'siteRef'='{self.site_id}' "
        query = query + f" GROUP BY tags #>>'{{ahuRef}}'"
        # TODO - add ahu without vavs
        result = self.execute_query(query)

        # 2. Query for ahus without vavs
        query = f"SELECT tags #>>'{{id}}' \
                FROM {self.equip_table} \
                WHERE tags->>'ahu'='m:' "
        if self.site_id:
            query = query + f" AND tags->>'siteRef'='{self.site_id}' "
        #print(query)
        ahu_list = [x[0] for x in self.execute_query(query)]
        #print(f"ahu_list is {ahu_list}")
        if len(ahu_list) > len(result):  # There were ahus without vavs. Add those to result
            for a in set(ahu_list) - set([x[0] for x in result]):
                result.append((a, []))

        # 3. query for vavs without ahus
        query = f"SELECT tags #>>'{{id}}' \
                        FROM {self.equip_table} \
                        WHERE tags->>'vav'='m:' AND (tags->>'ahuRef' is NULL OR tags->>'ahuRef' = '')"
        if self.site_id:
            query = query + f" AND tags->>'siteRef'='{self.site_id}' "
        #print(query)
        temp = self.execute_query(query)
        if temp:
            unmapped_vav_list = [x[0] for x in temp]
            #print(unmapped_vav_list)
            result.append(("", unmapped_vav_list))
            for vav in unmapped_vav_list:
                self.unmapped_device_details[vav] = {"type": "vav", "error": "Unable to find ahuRef"}
        return result

    def query_device_id_name(self, equip_id, equip_type):
        query = f"SELECT device_name, topic_name \
                  FROM {self.point_table} \
                  WHERE tags->>'equipRef'='{equip_id}' "
        if self.site_id:
            query = query + f" AND tags->>'siteRef'='{self.site_id}' "
        query = query + " LIMIT 1"

        result = self.execute_query(query)
        err_msg = None
        device_id = None
        topic_name = None
        object_name = None
        if result:
            device_id, topic_name = result[0]
            if equip_type == "vav" and equip_id in self.unmapped_device_details:
                # grab the topic_name to shed some light into ahu mapping
                self.unmapped_device_details[equip_id]["topic_name"] = topic_name
            try:
                object_name = self.get_object_name_from_topic(topic_name,
                                                              equip_type)
            except ValueError as v:
                err_msg = v.args[0]
                topic_name = device_id = object_name = None
        else:
            err_msg = f"Unable to find any points for {equip_id} from table:{self.point_table}"
        if err_msg:
            if not self.unmapped_device_details.get(equip_id):
                self.unmapped_device_details[equip_id] = dict()
            self.unmapped_device_details[equip_id]["type"] = equip_type
            self.unmapped_device_details[equip_id]["error"] = err_msg

        return topic_name, device_id, object_name

    def get_object_name_from_topic(self, topic_name, equip_type):
        # need device name only if device id is not unique
        if "attr_prop_object_name" in \
                self.config_template["driver_config"]["query"]:
            if equip_type == "ahu":
                part = topic_name.split("/")[-1]
                m = re.search(self.ahu_name_pattern, part)
                if m is None:
                    raise ValueError(
                        f"Unable to get ahu object name from {topic_name} "
                        f"using pattern {self.ahu_name_pattern}")
                match = m.group(0)
                return match.replace("[", "(").replace("]", ")")
            else:
                return topic_name.split("/")[-1].split(":")[0]
        return ""

    def execute_query(self, query):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query)
            result = cursor.fetchall()
            return result
        except Exception:
            raise
        finally:
            if cursor:
                cursor.close()

    def generate_config_from_template(self, equip_id, equip_type):
        topic_name, device_id, device_name = self.query_device_id_name(equip_id, equip_type)
        driver = copy.deepcopy(self.config_template)
        nf_query_format = driver["driver_config"]["query"]
        if "{device_id}" in nf_query_format and device_id is None or \
           "{obj_name}" in nf_query_format and device_name is None:
            if not self.unmapped_device_details.get(equip_id):
                self.unmapped_device_details[equip_id] = dict()
            self.unmapped_device_details[equip_id]["type"] = equip_type
            self.unmapped_device_details[equip_id]["topic_name"] = topic_name
            self.unmapped_device_details[equip_id]["error"] = "Unable to parse point topic name for " \
                                                              "nf device id and/or nf object name"
            return None
        else:
            nf_query = nf_query_format.format(device_id=device_id,
                                              obj_name=device_name)
            driver["driver_config"]["query"] = nf_query
        return driver

    def get_name_from_id(self, id):
        name = id.split(".")[-1]
        return name

def main():
    if len(sys.argv) != 2:
        print("script requires one argument - path to configuration file")
        exit()
    config_path = sys.argv[1]
    d = IntellimationDriverConfigGenerator(config_path)
    d.generate_configs()

if __name__ == '__main__':
    main()
