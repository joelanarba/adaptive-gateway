# Review and publish from Windows

These are commands for the repository owner to run **after reviewing the final
report**. The agent has not staged, committed or pushed. Run the first block from
PowerShell; it checks the explicit allowlist, scans intended files for common
secret patterns without printing matched values, and runs validation. Pattern
scanning cannot establish that arbitrary text contains no secrets; inspect the
whole source and staged diff. Never publish private settings, result directories
or environment files. The historical evidence and .gitignore are excluded.

```powershell
Set-Location -LiteralPath 'C:\dev\Projects\adaptive-gateway'
$ErrorActionPreference = 'Stop'
$py = 'C:\dev\Projects\adaptive-gateway\.venv\Scripts\python.exe'
$files = @(
  'ARCHITECTURE.md',
  'README.md',
  'benchmarks/fixture/Dockerfile',
  'benchmarks/fixture/app.py',
  'benchmarks/fixture/contract.py',
  'benchmarks/run_experiment.py',
  'benchmarks/run_trials.py',
  'benchmarks/run_study.py',
  'benchmarks/study_measurement.py',
  'benchmarks/study_analysis.py',
  'benchmarks/test_study.py',
  'docs/EXPERIMENT_METHODOLOGY.md',
  'docs/LIMITATIONS.md',
  'docs/RESULTS_TEMPLATE.md',
  'docs/HISTORICAL_EVIDENCE_AUDIT.md',
  'docs/EC2_RESEARCH_RUNBOOK.md',
  'docs/PUBLISHING.md',
  'docs/PRE_PUSH_REVIEW.md',
  'gateway/config.py',
  'gateway/main.py',
  'gateway/middleware/response_optimizer.py',
  'gateway/tests/test_benchmark_routing.py',
  'gateway/tests/test_response_modes.py'
)
git status --short
git diff --stat
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Working-tree diff check failed' }
git log -5 --oneline
$branch = git branch --show-current
if ($LASTEXITCODE -ne 0 -or $branch -ne 'master') { throw 'Expected master' }
$stagedBefore = @(git diff --cached --name-only)
if ($LASTEXITCODE -ne 0 -or $stagedBefore.Count -ne 0) { throw 'Review existing staging before continuing' }
$changed = @((git diff --name-only HEAD), (git ls-files --others --exclude-standard)) | ForEach-Object { $_ }
$unexpected = @($changed | Where-Object { $_ -and $_ -notin $files })
if ($unexpected.Count) { $unexpected; throw 'Changes outside intended file list need review' }
if ($files | Where-Object { $_ -match '(^|/)(\.env[^/]*|\.venv[^/]*|bin|lib|lib64|runs|results|__pycache__|\.tmp)(/|$)|\.(pem|key)$|(^|/)pyvenv\.cfg$' }) { throw 'Prohibited allowlist entry' }
$scan = @"
import json,pathlib,re,subprocess,sys
files=json.load(sys.stdin)
patterns=[
 r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
 r'\bgh[pousr]_[A-Za-z0-9]{30,}\b',
 r'\bgithub_pat_[A-Za-z0-9_]{40,}\b',
 r'\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b',
 r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
 r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{16,}\b',
]
findings=[]
for name in files:
 for number,line in enumerate(pathlib.Path(name).read_text(encoding='utf-8').splitlines(),1):
  if any(re.search(pattern,line) for pattern in patterns): findings.append(f'{name}:{number}')
if findings:
 print('Potential secrets (values withheld):',*findings,sep='\n');sys.exit(1)
# Compare committed evidence using checkout-aware text line endings; never rewrite.
evidence=subprocess.check_output(['git','ls-files','research/evidence/preliminary_2026-10-18'],text=True).splitlines()
assert len(evidence)==19
for name in evidence:
 old=subprocess.check_output(['git','show','HEAD:'+name])
 assert old.replace(b'\r\n',b'\n')==pathlib.Path(name).read_bytes().replace(b'\r\n',b'\n'), 'Historical evidence differs: '+name
assert not subprocess.check_output(['git','diff','--name-only','HEAD','--','.gitignore'],text=True).strip()
print('Allowlisted text scan and evidence/ignore checks passed; inspect manually too.')
"@
$files | ConvertTo-Json -Compress | & $py -c $scan
if ($LASTEXITCODE -ne 0) { throw 'Secret/evidence check failed' }
$env:BLACK_CACHE_DIR = Join-Path $PWD '.tmp\black'
$env:TEMP = Join-Path $PWD '.tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
& $py -m pytest -q -o cache_dir=.test-cache --tb=short
if ($LASTEXITCODE -ne 0) { throw 'pytest failed' }
& $py -m ruff check .
if ($LASTEXITCODE -ne 0) { throw 'Ruff failed' }
& $py -m black --check . --workers 1
if ($LASTEXITCODE -ne 0) { throw 'Black failed' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Diff check failed' }
```

Inspect `git diff` **and all new files**, including this runbook and the
EC2 procedure. git diff alone omits untracked file contents. The following block
stages exactly the reviewed allowlist; it does not commit or push:

```powershell
git add -- $files
if ($LASTEXITCODE -ne 0) { throw 'Explicit staging failed' }
$staged = @(git diff --cached --name-only)
if ($LASTEXITCODE -ne 0 -or @($staged | Where-Object { $_ -notin $files }).Count) { throw 'Unexpected staged paths' }
if (@($files | Where-Object { $_ -notin $staged }).Count) { throw 'Allowlist changed; review before proceeding' }
git diff --cached --stat
git diff --cached --check
if ($LASTEXITCODE -ne 0) { throw 'Staged whitespace check failed' }
git diff --cached --name-only
git diff --cached
```

**Stop here and inspect the complete staged diff.** Resolve every potential secret,
wrong path and unexpected change. Do not commit automatically after this block.
If you independently choose to commit the reviewed files, run this separate block:

```powershell
git commit -m "Add controlled response-adaptation research protocol and validation"
if ($LASTEXITCODE -ne 0) { throw 'Commit failed' }
git show --stat --oneline HEAD
git status --short
```

Only after reviewing that commit, choose to run the separate push block. Verify
origin refers to joelanarba/adaptive-gateway without exposing credentials in output;
use your credential manager. Fetching here updates remote-tracking refs only.

```powershell
if ((git branch --show-current) -ne 'master') { throw 'Expected master' }
git fetch origin master
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed' }
git merge-base --is-ancestor origin/master HEAD
if ($LASTEXITCODE -ne 0) { throw 'Remote advanced or history diverged; reconcile and re-review' }
git log --oneline origin/master..HEAD
# Review all listed commits; pushing master publishes all of them.
git push origin master
if ($LASTEXITCODE -ne 0) { throw 'Push failed; inspect without forcing' }
git rev-parse HEAD
```

Record the resulting full commit hash for the EC2 runbook. A rejected normal push
must be investigated; do not force it or rewrite remote history. No experiment
results, virtual environment, private settings or historical snapshot changes are
part of this explicit staging list.
