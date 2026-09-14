"""Application request/result contracts remain usable independently of execution services."""

import json
import subprocess
import sys


def test_contracts_have_compatible_public_reexports_without_loading_plotting():
    script = """
import json, sys
from cpdatakit.application import contracts
from cpdatakit import application
assert 'matplotlib.pyplot' not in sys.modules
request = contracts.ConvertRequest('input.csv', 'curve', 'output.h5')
assert str(request.output) == 'output.h5'
result = contracts.ServiceResult[int]('contract', 'succeeded', value=3)
assert result.to_dict()['value'] == 3
from cpdatakit.application import services
names = [
    'ServiceError', 'ServiceResult', 'DatasetRequest', 'ImportInspectRequest',
    'ResolveSchemaRequest', 'ConvertRequest', 'ReportRequest', 'ComparisonRequest',
    'PlotRequest', 'SchemaDiffRequest', 'ResolvedSchemaMapping', 'ValidationSummary',
    'ConversionOutcome', 'ReportOutcome', 'ComparisonOutcome', 'PlotOutcome',
    'SchemaDiffOutcome',
]
for name in names:
    assert getattr(contracts, name) is getattr(services, name)
    assert getattr(application, name) is getattr(contracts, name)
print(json.dumps({'names': len(names), 'value': result.to_dict()['value']}))
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"names": 17, "value": 3}
