# RAG System Optimizer

This repository implements **Method v3**: a pre-registered, agent-driven optimization protocol for RAG (Retrieval-Augmented Generation) systems. It automatically searches for performance improvements through hypothesis generation, implementation, screening, and confirmation on held-out data.

## Overview

Method v3 is a complete implementation of the protocol described in the accompanying paper. Key features:

- **Pre-registered protocol**: All statistical thresholds, sample sizes, and decision rules are fixed before the run
- **Automatic hypothesis generation**: An analyst agent reads failure cases and proposes targeted fixes
- **Parallel candidate evaluation**: Multiple hypotheses tested concurrently with screening on a validation pool
- **Fresh-batch confirmation**: Adopted improvements are confirmed on previously unseen test cases to control for selection bias
- **Sealed lineage evaluation**: Final performance measured on a sealed holdout set with family-wise error control

The system is designed to optimize a target RAG system (e.g., Silicon Notebook) while maintaining statistical rigor and preventing overfitting.

## Architecture

**Core optimizer** (`src/rag_eval/optimizer/`):
- `loop.py`: Main optimization loop with screening, confirmation, and halt logic
- `stats.py`: Statistical decision rules (Ladder-m screening, Holm-Bonferroni confirmation)
- `adapter.py`: Abstract interface to the target RAG system
- `sn_adapter.py`: Concrete adapter for Silicon Notebook via workbench API
- `ledger_io.py`: Durable state management and resume support

**Workbench** (`src/rag_eval/workbench/`):
- HTTP API server for managing optimization runs
- Integration with Silicon Notebook v2 candidate/measurement services
- Automatic run registration and monitoring
- UI for viewing lineage curves and sealed results

**CLI** (`src/rag_eval/optimizer/cli.py`):
- `sn-method-v3 run`: Start an optimization run
- `sn-method-v3 sealed`: Evaluate sealed lineage after run completion
- `sn-method-v3 status`: Monitor run progress

## Documentation

- [Method v3 Runbook](docs/method-v3-runbook.md): Protocol parameters, pre-registration, and execution guide
- [Workbench API](docs/evaluation-improvement-workbench-prd.md): Server setup and API reference
- [Failure Ledger](docs/failure-ledger.md): Error handling and recovery procedures

## Installation

```bash
# Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install the optimizer package with development dependencies
pip install -e '.[dev]'
```

For workbench UI development:
```bash
cd apps/sn-workbench
npm install
npm run dev  # Start development server
npm run build  # Build for production
```

## Quick Start

### Running an optimization

```bash
# Start the workbench server
python -m rag_eval.workbench.cli serve --config config.json --port 8093

# In another terminal, start an optimization run
sn-method-v3 run \
  --iteration-id my-run \
  --protocol protocol.json \
  --state-dir ./runs/my-run
```

### Evaluating the sealed lineage

After a run completes:

```bash
sn-method-v3 sealed \
  --state-dir ./runs/my-run \
  --confirm  # Confirm to run the evaluation
```

### Monitoring progress

```bash
# Check run status
sn-method-v3 status --state-dir ./runs/my-run

# Or view in the workbench UI at http://localhost:8093
```

## Testing

```bash
# Run Python tests
pytest tests/

# Run workbench UI tests
cd apps/sn-workbench
npm test
```

## Configuration

See [Method v3 Runbook](docs/method-v3-runbook.md) for detailed protocol parameter configuration and pre-registration guidelines.

## License

See LICENSE file for details.
