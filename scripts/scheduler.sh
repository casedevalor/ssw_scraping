#!/usr/bin/env bash
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"
set -e

# get the directory of the script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$PROJECT_ROOT"

# --- NOVO: Caminho para o main.py ---
main_script="$PROJECT_ROOT/main.py"
# Running in a virtual environment
UV_BIN="/home/swtbot/.local/bin/uv"
UV="$UV_BIN run python"

DOMAIN_NAME="GUM" 

# Variables for the current time
current_hour=$(date +%H)
current_minute=$(date +%M)
current_day=$(date +%A) 

# Conditions for running the scripts (LÓGICA IDÊNTICA)
if [ "$current_hour" -eq 2 ] && [ "$current_minute" -ge 50 ]; then   
    # call "Workflow Start"
    $UV "$main_script" WF_ALL "$DOMAIN_NAME"

elif [ "$current_hour" -eq 1 ] && [ "$current_minute" -eq 15 ]; then
    # call "Workflow 930"
    $UV "$main_script" WF_DAILY "$DOMAIN_NAME"

elif [ "$current_hour" -ge 8 ] && [ "$current_hour" -le 19 ] && [ "$current_minute" -eq 15 ]; then
    # call "Workflow 930" de novo
    $UV "$main_script" WF_DAILY "$DOMAIN_NAME"

elif [ "$current_hour" -eq 17 ] && [ "$current_minute" -eq 0 ]; then
    # call "Workflow 455 Weekend"
    $UV "$main_script" WF_WE "$DOMAIN_NAME"
fi