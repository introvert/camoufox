#!/bin/bash

# Function to check if an argument is valid
check_arg() {
    case "$1" in
        --headful)
            return 0
            ;;
        --executable-path)
            if [ -z "$2" ]; then
                echo "Error: --executable-path requires a path argument"
                return 1
            elif [ ! -e "$2" ]; then
                echo "Error: The path specified for --executable-path does not exist: $2"
                return 1
            fi
            return 0
            ;;
        *)
            echo "Error: Invalid argument '$1'. Only --headful and --executable-path are allowed."
            return 1
            ;;
    esac
}

# Check if venv exists, if not run setup-venv.sh
if [ ! -d "venv" ]; then
    echo "Virtual environment not found. Running setup-venv.sh..."
    bash ./setup-venv.sh
    if [ $? -ne 0 ]; then
        echo "Failed to set up virtual environment. Exiting."
        exit 1
    fi
fi

# Validate arguments
VALID_ARGS=("--headless")  # Set headless as default
i=1
while [ $i -le $# ]; do
    arg="$1"
    case "$arg" in
        --executable-path)
            shift  # move to the next argument
            if [ -z "$1" ]; then
                echo "Error: --executable-path requires a path argument"
                exit 1
            fi
            if check_arg "--executable-path" "$1"; then
                export CAMOUFOX_EXECUTABLE_PATH="$1"
                shift
            else
                exit 1
            fi
            ;;
        --headful)
            if check_arg "$arg"; then
                VALID_ARGS=()  # Remove default --headless
                shift
            else
                exit 1
            fi
            ;;
        *)
            echo "Error: Invalid argument '$arg'. Only --headful and --executable-path are allowed."
            exit 1
            ;;
    esac
done

# Run pytest with validated arguments
echo "Running pytest with arguments: ${VALID_ARGS[@]}"
if [ -n "$CAMOUFOX_EXECUTABLE_PATH" ]; then
    echo "CAMOUFOX_EXECUTABLE_PATH set to: $CAMOUFOX_EXECUTABLE_PATH"
fi

# async/test_headful.py launches real windows whatever --headless says, so those
# seven tests need a display even on a headless run. Supply a virtual one when
# the machine has no X server, which is the normal case on a build box.
RUNNER=()
if [ -z "$DISPLAY" ]; then
    if command -v xvfb-run >/dev/null 2>&1; then
        echo "No DISPLAY set; running under xvfb-run so the headful tests can open windows."
        RUNNER=(xvfb-run -a)
    else
        echo "WARNING: no DISPLAY and no xvfb-run; async/test_headful.py will fail." >&2
        echo "         Install it with: apt-get install -y xvfb" >&2
    fi
fi

echo "${RUNNER[@]}" venv/bin/pytest -vv "${VALID_ARGS[@]}" async/
"${RUNNER[@]}" venv/bin/pytest -vv "${VALID_ARGS[@]}" async/