#!/usr/bin/env bash
# filename: publish_prep.sh
set -euo pipefail

# Safely resolve the absolute path of the directory containing this script
SCRIPT_DIR="$(dirname "$(readlink -f "$0" 2>/dev/null || perl -MCwd -e 'print Cwd::abs_path(shift)' "$0")")"

# Force the execution workspace to be the script's root directory
cd "${SCRIPT_DIR}"

echo "=== 1. Cleaning Old Build Artifacts ==="
rm -rf build/ dist/ *.egg-info src/*.egg-info

echo "=== 2. Environment Verification & Tool Setup ==="
python3 -m pip install --upgrade --quiet build twine pytest

echo "Installing package locally in editable mode for verification..."
# Install the root directory package in editable mode to open up the search path structures natively
python3 -m pip install -e . --quiet

# Extract name from pyproject.toml under [project] and replace '-' with '_'
MODULE_NAME=$(grep -m 1 "^name *=" pyproject.toml | sed -E 's/name *= *"([^"]*)".*/\1/' | tr '-' '_')

echo "=== 3. Executing Test Suite ==="
if [ -d "tests" ]; then
    # pytest now works natively out of the box because the package is linked globally
    python3 -m pytest tests/
else
    echo "No 'tests/' directory found. Running quick import smoke test for '${MODULE_NAME}'..."
    # No more fragile PYTHONPATH tracking hacks needed here
    python3 -c "import ${MODULE_NAME}; print('Module import successful.')"
fi

echo "=== 4. Packaging Wheel & Source Distribution ==="
python3 -m build

echo "=== 5. Checking Package Integrity with Twine ==="
python3 -m twine check dist/*

echo ""
echo "=="
echo " BUILD & VERIFICATION SUCCESSFUL"
echo "=="
echo "To publish to PyPI, run the following commands:"
echo ""
echo "  # Option A: TestPyPI (Recommended first step)"
echo "  python3 -m twine upload --repository testpypi dist/*"
echo ""
echo "  # Option B: Official PyPI Production Release"
echo "  python3 -m twine upload dist/*"
echo "========================================================"
