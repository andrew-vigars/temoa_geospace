import csv
import json

import pytest

from geocanoe.acquisition.era5_renewables import manifest, sha256
from geocanoe.preprocessing.atb import SELECTION_COLUMNS, build_atb_subset


def test_exact_family_selection_preserves_cases_columns_and_rejects_unknown(tmp_path):
    source = tmp_path / "master.csv"
    fields = [*SELECTION_COLUMNS, "value", "ref_evidence_type"]
    rows = []
    for technology, case in [
        ("UtilityPV", "R&D"),
        ("UtilityPV", "Exp"),
        ("CommPV", "R&D"),
    ]:
        row = dict.fromkeys(fields, "*")
        row.update(
            technology=technology,
            core_metric_case=case,
            value="123.456",
            ref_evidence_type="original",
        )
        rows.append(row)
    with source.open("w", newline="") as f:
        writer = csv.DictWriter(f, fields)
        writer.writeheader()
        writer.writerows(rows)
    record = tmp_path / "source.json"
    manifest(record, {"edition": 2025, "revision": "v1.0.0", "sha256": sha256(source)})
    config = tmp_path / "subset.toml"
    config.write_text(
        f'source="{source.as_posix()}"\nmanifest="{record.as_posix()}"\noutput_dir="{tmp_path.as_posix()}/out"\ntechnologies=["UtilityPV"]\n'
    )
    output = build_atb_subset(config)
    with output.open() as f:
        reader = csv.DictReader(f)
        selected = list(reader)
        assert reader.fieldnames == fields
    assert selected == rows[:2]
    audit = json.loads((output.parent / "selection_manifest.json").read_text())
    assert audit["rows_by_technology"] == {"UtilityPV": 2}
    assert not audit["model_ready"]
    config.write_text(
        config.read_text().replace(
            'technologies=["UtilityPV"]', 'technologies=["UtilityPV", "typo"]'
        )
    )
    with pytest.raises(ValueError, match="absent"):
        build_atb_subset(config)
    with output.open() as f:
        assert list(csv.DictReader(f)) == selected
    source.write_text("corrupt")
    with pytest.raises(ValueError, match="checksum"):
        build_atb_subset(config)
