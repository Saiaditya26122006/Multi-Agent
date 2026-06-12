#!/usr/bin/env bash
#
# Run grounded evaluation with timestamped logging
# Output is both displayed and saved to /tmp
#

set -euo pipefail

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="/tmp/grounded_eval_${TIMESTAMP}.log"

echo "Starting grounded evaluation at $(date)"
echo "Logging to: ${LOG_FILE}"
echo ""

cd "$(dirname "$0")/.."

python3 -c "
import asyncio
from evaluation.run_grounded_eval import run_grounded_eval
asyncio.run(run_grounded_eval())
" 2>&1 | tee "${LOG_FILE}"

EXIT_CODE=${PIPESTATUS[0]}

echo ""
echo "Evaluation completed at $(date)"
echo "Log saved to: ${LOG_FILE}"
echo "Exit code: ${EXIT_CODE}"

exit ${EXIT_CODE}
