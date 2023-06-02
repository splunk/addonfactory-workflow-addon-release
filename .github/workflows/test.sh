#!/bin/bash

# GitHub repository details
OWNER="your_username"
REPO="your_repository"

# GitHub API endpoint for labels
API_URL="https://api.github.com/repos/$OWNER/$REPO/labels"

# GitHub personal access token (replace with your own)
TOKEN="your_personal_access_token"


# Array of label details
LABELS=(
  '{"name": "execute_knowledge", "description": "Trigger execution of knowledge objects tests", "color": "4040F2"}'
  '{"name": "execute_ui", "description": "Trigger execution of ui tests", "color": "F79D34"}'
  '{"name": "execute_modinput_functional", "description": "Trigger execution of modinput functional tests", "color": "7ED321"}'
  '{"name": "execute_scripted_inputs", "description": "Trigger execution of scripted inputs tests", "color": "9013FE"}'
  '{"name": "execute_escu", "description": "Trigger execution of escu tests", "color": "F5A623"}'
  '{"name": "execute_requirement_test", "description": "Trigger execution of requirement tests", "color": "F8E71C"}'
  '{"name": "execute_all_tests", "description": "Trigger execution of all tests types", "color": "006B76"}'
)

# Loop through the labels and add them to the repository
for label in "${LABELS[@]}"; do
    gh label create "$(echo "$label" | jq -r '.| .name')" --color "$(echo "$label" | jq -r '.| .color')" --description "$(echo "$label" | jq '.| .description')" --force
done
