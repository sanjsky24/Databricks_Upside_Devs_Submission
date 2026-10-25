#!/bin/zsh
# Gives the DengueRadar app's service principal what the chat box needs: read-only access to the
# 14 tables the Genie space uses, CAN_USE on the starter SQL warehouse, CAN_RUN on the Genie space.
set -e
export PATH=$HOME/.local/bin:$PATH
SP=86ba7317-51d2-4e17-9522-51e702e1e712          # app "dengueradar" service principal
WH=18e372f3f7575204                                # Serverless Starter Warehouse
SPACE=01f1d07c20ef1f75b94049d6e4b1f908             # DengueRadar Genie
run() {
  body=$(python3 -c "import json,sys; print(json.dumps({'warehouse_id':'$WH','statement':sys.argv[1],'wait_timeout':'50s'}))" "$1")
  databricks api post /api/2.0/sql/statements --json "$body" | python3 -c "import json,sys; r=json.load(sys.stdin); s=r['status']['state']; print(s, sys.argv[1]); sys.exit(s!='SUCCEEDED')" "$1"
}
run "GRANT USE CATALOG ON CATALOG workspace TO \`$SP\`"
run "GRANT USE SCHEMA ON SCHEMA workspace.dengue TO \`$SP\`"
for t in gold_forecast_latest silver_clusters gold_area_levels gold_track_record gold_model_test_headline \
         gold_model_test_risk gold_model_test_new_clusters gold_model_test_by_year gold_team_plan gold_block_priority \
         gold_national_weekly gold_weather_monthly bronze_nea_high_aedes_areas_latest silver_planning_areas; do
  run "GRANT SELECT ON TABLE workspace.dengue.$t TO \`$SP\`"
done
databricks api patch /api/2.0/permissions/sql/warehouses/$WH \
  --json "{\"access_control_list\":[{\"service_principal_name\":\"$SP\",\"permission_level\":\"CAN_USE\"}]}" >/dev/null && echo "SUCCEEDED warehouse CAN_USE"
databricks api patch /api/2.0/permissions/genie/$SPACE \
  --json "{\"access_control_list\":[{\"service_principal_name\":\"$SP\",\"permission_level\":\"CAN_RUN\"}]}" >/dev/null && echo "SUCCEEDED Genie space CAN_RUN"
