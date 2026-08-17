#!/usr/bin/env bash
# filename: publish_prep.sh
set -euo pipefail

echo "=== 1. Cleaning Old Build Artifacts ==="
rm -rf build/ dist/ *.egg-info src/*.egg-info

echo "=== 2. Environment Verification & Tool Setup ==="
python3 -m pip install --upgrade --quiet build twine pytest

echo "=== 3. Executing Test Suite ==="
if [ -d "tests" ]; then
python3 -m pytest tests/
else
echo "No 'tests/' directory found. Running quick import smoke test..."
python3 -c "import image_bridge_toolkit; print('Module import successful.')"
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