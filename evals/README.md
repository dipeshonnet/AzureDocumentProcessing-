# Admissions AI Evals

Run the mock evaluation harness from the repository root:

```powershell
python -m evals.run_evals
```

If `make` is available:

```powershell
make eval
```

On Windows without `make`:

```powershell
.\scripts\run-evals.ps1
```

The default provider is `mock`, so no Azure services are called.

To run the rubric-scoring portion against real Azure OpenAI, explicitly opt in:

```powershell
python -m evals.run_evals --provider azure --enable-azure-openai
```

or:

```powershell
$env:EVAL_ENABLE_AZURE_OPENAI = "true"
python -m evals.run_evals --provider azure
```

Azure evals require the normal Azure OpenAI environment variables to be configured.

For case descriptions, checks, and result interpretation, see:

```text
docs/admissions-ai-evals.md
```
