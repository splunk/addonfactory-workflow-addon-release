import yaml
import os
import configparser
import re

GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")

# Parse app.conf get the appid of the TA.
config = configparser.ConfigParser(strict=False)
config.read("package/default/app.conf")
APP_ID = config.get("id", "name")
APP_LABEL = config.get("ui", "label")

print(f"APP_ID = {APP_ID}, APP_LABEL = {APP_LABEL}")

# Read the file and remove trailing backslashes
with open("package/default/props.conf", "r") as f:
    content = f.read()

# Remove trailing backslashes followed by a newline
updated_content = re.sub(r"\\\n", "", content)

# Write the cleaned content to a new file
with open("package/default/props.conf", "w") as f:
    f.write(updated_content)

# Parse props.conf and collect all the sourcetypes in a list.
config = configparser.ConfigParser(strict=False)
config.read("package/default/props.conf")
sourcetypes = config.sections()

# Load the YAML content
with open("security_content/contentctl.yml", "r") as file:
    data = yaml.safe_load(file)

    app_found = False
    for app in data["apps"]:
        if app['appid'] == APP_ID or APP_ID in app['hardcoded_path'] or GITHUB_REPOSITORY in app['hardcoded_path'] or app["title"] == APP_LABEL or (app['appid'] == "PALO_ALTO_NETWORKS_ADD_ON_FOR_SPLUNK" and APP_ID == "Splunk_TA_paloalto_networks"):
            app['hardcoded_path'] = "${{ env.TA_BUILD_PATH }}"
            app_found = True

        if not app_found:
            print(f"App not found in contentctl.yml file. Exiting.")
            exit(127)

# Write the modified data to the contentctl.yml file
with open("security_content/contentctl.yml", "w") as file:
    yaml.dump(data, file, sort_keys=False)

# Filter out the detections based on the collected sourcetypes
base_dir = "security_content/detections"
detection_files = ""

for root, dirs, files in os.walk(base_dir):
    for file in files:
        file_path = os.path.join(root, file)

        try:
            with open(file_path, "r") as yaml_file:
                file_content = yaml.safe_load(yaml_file)
                if "deprecated" not in file_path and (
                        file_content["tests"][0]["attack_data"][0]["sourcetype"] in sourcetypes or file_content["tests"][0]["attack_data"][0]["source"] in sourcetypes):
                    detection_files += file_path.replace("security_content/", "") + " "

        except Exception as e:
            continue

# Save detection_files as an output variable
with open(os.getenv('GITHUB_OUTPUT'), 'w') as output_file:
    output_file.write(f"DETECTION_FILES={detection_files}")

print(f"Filtered Detection files = {detection_files}")
