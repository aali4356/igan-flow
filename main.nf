nextflow.enable.dsl = 2

include { VALIDATE_MANIFEST; ACQUIRE; CONVERT; QC; EXPORT; VERIFY_DONOR; AGGREGATE; REPORT } from './modules/local/workflow'

workflow {
    def processing_code = ['pipeline.py', 'common.py', 'integrity.py'].collect { file("${projectDir}/lib/${it}", checkIfExists: true) }
    def verifier_code = ['verify_donor.py', 'integrity.py'].collect { file("${projectDir}/lib/${it}", checkIfExists: true) }
    def aggregate_code = ['aggregate.py', 'common.py', 'integrity.py'].collect { file("${projectDir}/lib/${it}", checkIfExists: true) }
    def report_code = ['render_report.py', 'report.html.j2', 'common.py', 'integrity.py'].collect { file("${projectDir}/lib/${it}", checkIfExists: true) }
    def runtime = [file("${projectDir}/requirements.lock"), file("${projectDir}/assets/toolchain.json"), file("${projectDir}/scripts/env.sh"), file(params.runtime_manifest, checkIfExists: true)]
    def research = ['GSE285335_sample_manifest.tsv', 'GSE285335_series_matrix.txt.gz'].collect { file("${projectDir}/research/${it}", checkIfExists: true) }
    def settings = file(params.settings, checkIfExists: true)
    VALIDATE_MANIFEST(file(params.input, checkIfExists: true), research, processing_code, runtime)
    def records = VALIDATE_MANIFEST.out.records.flatten().map { record ->
        def row = new groovy.json.JsonSlurperClassic().parseText(record.text)
        def sources = ['matrix_url', 'features_url', 'barcodes_url']
            .collect { row[it] }.findAll { it.startsWith('file:') }
            .collect { file(new java.net.URI(it).getPath(), checkIfExists: true) }
        tuple(row.sample_id, record, sources)
    }
    ACQUIRE(records, processing_code, runtime)
    CONVERT(ACQUIRE.out, settings, processing_code, runtime)
    QC(CONVERT.out, settings, processing_code, runtime)
    EXPORT(CONVERT.out.join(QC.out, failOnDuplicate: true, failOnMismatch: true), processing_code, runtime)
    VERIFY_DONOR(ACQUIRE.out.join(EXPORT.out, failOnDuplicate: true, failOnMismatch: true), settings, verifier_code, runtime)
    AGGREGATE(EXPORT.out.map { id, dir -> dir }.collect(),
              VERIFY_DONOR.out.verified.map { id, result -> result }.collect(),
              VERIFY_DONOR.out.seal.collect(),
              VALIDATE_MANIFEST.out.audit, settings, aggregate_code, runtime)
    REPORT(AGGREGATE.out, report_code, runtime)
}
