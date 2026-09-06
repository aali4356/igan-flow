#!/usr/bin/env python3
"""Refresh small GSE285335 source evidence; optionally inspect one count trio.

Uses curl with TLS verification. Default fetches only metadata, file lists and
small barcode/feature lists; it never downloads the 1.59 GB series archive.
Run: python3 research/inspect_geo_dataset.py --output-dir /tmp/igan-flow-source-refresh
Add --inspect-sample GSM8700986 to inspect one compressed count matrix header.
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import subprocess


def fetch(url):
    return subprocess.check_output([
        'curl', '--location', '--fail', '--retry', '2', '--max-time', '60',
        '--silent', '--show-error', url,
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect-sample')
    parser.add_argument('--output-dir', type=Path, required=True,
                        help='Separate directory for refreshed evidence; preserve the original snapshot.')
    args = parser.parse_args()
    out = args.output_dir.expanduser().resolve()
    if out == Path(__file__).resolve().parent:
        parser.error('Use a separate output directory to preserve the original research snapshot.')
    out.mkdir(parents=True, exist_ok=True)
    base = 'https://ftp.ncbi.nlm.nih.gov/geo/series/GSE285nnn/GSE285335/'
    matrix_url = base + 'matrix/GSE285335_series_matrix.txt.gz'
    filelist_url = base + 'suppl/filelist.txt'
    metadata = fetch(matrix_url)
    files = fetch(filelist_url)
    (out / 'GSE285335_series_matrix.txt.gz').write_bytes(metadata)
    (out / 'GSE285335_filelist.txt').write_bytes(files)
    rows = [r for r in csv.reader(io.StringIO(gzip.decompress(metadata).decode()), delimiter='\t') if r]
    rm = {r[0]: r[1:] for r in rows}
    chars = [r[1:] for r in rows if r[0] == '!Sample_characteristics_ch1']
    groups = next(r for r in chars if r[0].startswith('disease state: '))
    batches = next(r for r in chars if r[0].startswith('batch: '))
    supplements = [r[1:] for r in rows if r[0].startswith('!Sample_supplementary_file')]
    sizes = {r[1]: int(r[3]) for r in csv.reader(io.StringIO(files.decode()), delimiter='\t') if r and r[0] == 'File'}

    def sample(i):
        record = {
            'sample_id': rm['!Sample_geo_accession'][i],
            'source_title': rm['!Sample_title'][i],
            'group': groups[i].split(': ', 1)[1],
            'batch': batches[i].split(': ', 1)[1],
        }
        for row in supplements:
            url = row[i].replace('ftp://', 'https://', 1)
            kind = next(k for k in ('barcodes', 'features', 'matrix') if k in url.rsplit('/', 1)[1])
            record[kind + '_url'] = url
            record[kind + '_compressed_bytes'] = sizes[url.rsplit('/', 1)[1]]
        barcodes = fetch(record['barcodes_url'])
        record['supplied_barcodes'] = len(gzip.decompress(barcodes).splitlines())
        record['barcodes_sha256'] = hashlib.sha256(barcodes).hexdigest()
        features = fetch(record['features_url'])
        feature_rows = [line.split('\t') for line in gzip.decompress(features).decode().splitlines()]
        ids = [row[0] for row in feature_rows]
        record['feature_rows'] = len(feature_rows)
        record['unique_feature_ids'] = len(set(ids))
        record['duplicate_feature_ids_json'] = json.dumps({k: v for k, v in Counter(ids).items() if v > 1})
        record['features_sha256'] = hashlib.sha256(features).hexdigest()
        record['feature_content_sha256'] = hashlib.sha256(gzip.decompress(features)).hexdigest()
        record['feature_columns_1_and_2_identical'] = all(row[0] == row[1] for row in feature_rows)
        record['feature_types_json'] = json.dumps(dict(Counter(row[2] for row in feature_rows)))
        return record

    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(sample, range(len(rm['!Sample_geo_accession']))))
    with (out / 'GSE285335_sample_manifest.tsv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]), delimiter='\t')
        writer.writeheader()
        writer.writerows(records)
    summary = {
        'retrieved_utc': datetime.now(timezone.utc).isoformat(),
        'series_url': 'https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE285335',
        'metadata_url': matrix_url,
        'metadata_sha256': hashlib.sha256(metadata).hexdigest(),
        'filelist_url': filelist_url,
        'filelist_sha256': hashlib.sha256(files).hexdigest(),
        'n_samples': len(records),
        'groups': dict(Counter(r['group'] for r in records)),
        'group_by_batch': dict(Counter(r['group'] + '/' + r['batch'] for r in records)),
        'supplied_barcodes_total': sum(r['supplied_barcodes'] for r in records),
        'supplementary_file_bytes_total': sum(sizes.values()),
        'feature_content_hashes': dict(Counter(r['feature_content_sha256'] for r in records)),
        'feature_rows': dict(Counter(r['feature_rows'] for r in records)),
        'unique_feature_ids': dict(Counter(r['unique_feature_ids'] for r in records)),
        'all_feature_columns_1_and_2_identical': all(r['feature_columns_1_and_2_identical'] for r in records),
        'note': 'Supplied barcode counts are before this project QC; not independently verified viable-cell counts. Full expression matrices were not downloaded by default.',
    }
    (out / 'GSE285335_source_summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    if args.inspect_sample:
        record = next(r for r in records if r['sample_id'] == args.inspect_sample)
        inspection = {'sample_id': args.inspect_sample, 'files': {}}
        for kind in ('matrix', 'barcodes', 'features'):
            raw = fetch(record[kind + '_url'])
            item = {'url': record[kind + '_url'], 'compressed_bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as handle:
                if kind == 'matrix':
                    item['head'] = [handle.readline().decode().strip() for _ in range(7)]
                else:
                    lines = handle.read().decode().splitlines()
                    item['rows'] = len(lines)
                    item['head'] = lines[:5]
                    if kind == 'features':
                        ids = [line.split('\t')[0] for line in lines]
                        item['unique_feature_ids'] = len(set(ids))
                        item['duplicate_feature_ids'] = {k: v for k, v in Counter(ids).items() if v > 1}
            inspection['files'][kind] = item
        (out / (args.inspect_sample + '_inspection.json')).write_text(json.dumps(inspection, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
