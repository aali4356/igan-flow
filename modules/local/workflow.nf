// Quote all dynamic shell arguments, including paths with apostrophes.
def quoteArg(value) {
    return "'" + value.toString().replace("'", "'\\''") + "'"
}

process VALIDATE_MANIFEST {
    input:
    path manifest
    path research
    path code
    path runtime
    output:
    path 'manifest/records/*.json', emit: records
    path 'manifest/manifest_validation.json', emit: audit
    script:
    def options = params.synthetic ? '--synthetic' : ''
    def selection = params.sample_id ? "--sample-id ${quoteArg(params.sample_id)}" : ''
    """
    python pipeline.py manifest --manifest ${quoteArg(manifest)} --research . ${options} ${selection} --out manifest
    """
}

process ACQUIRE {
    tag "$id"
    maxForks 2
    input:
    tuple val(id), path(record), path(local_sources)
    path code
    path runtime
    output:
    tuple val(id), path("${id}.inputs")
    script:
    """
    python pipeline.py acquire --record ${quoteArg(record)} --cache ${quoteArg(params.cache)} --out '${id}.inputs'
    """
}

process CONVERT {
    tag "$id"
    label 'heavy'
    input:
    tuple val(id), path(source)
    path settings
    path code
    path runtime
    output:
    tuple val(id), path("${id}.validated")
    script:
    """
    python pipeline.py convert --source ${quoteArg(source)} --settings ${quoteArg(settings)} --threads ${task.cpus} --out '${id}.validated'
    """
}

process QC {
    tag "$id"
    label 'heavy'
    input:
    tuple val(id), path(source)
    path settings
    path code
    path runtime
    output:
    tuple val(id), path("${id}.qc")
    script:
    """
    python pipeline.py qc --source ${quoteArg(source)} --settings ${quoteArg(settings)} --threads ${task.cpus} --out '${id}.qc'
    """
}

process EXPORT {
    tag "$id"
    label 'heavy'
    input:
    tuple val(id), path(source), path(qc)
    path code
    path runtime
    output:
    tuple val(id), path("${id}.export")
    script:
    """
    python pipeline.py export --source ${quoteArg(source)} --qc ${quoteArg(qc)} --threads ${task.cpus} --out '${id}.export'
    """
}

process VERIFY_DONOR {
    tag "$id"
    label 'heavy'
    input:
    tuple val(id), path(source), path(exported)
    path settings
    path code
    path runtime
    output:
    tuple val(id), path("${id}.verification.json"), emit: verified
    path "${id}.verification.json.sha256", emit: seal
    script:
    """
    python verify_donor.py --source ${quoteArg(source)} --export ${quoteArg(exported)} --settings ${quoteArg(settings)} --threads ${task.cpus} --out '${id}.verification.json'
    """
}

process AGGREGATE {
    label 'heavy'
    input:
    path donors
    path verifications
    path verification_seals
    path manifest_audit
    path settings
    path code
    path runtime
    output:
    path 'summary'
    script:
    """
    python aggregate.py --donors ${donors.collect { quoteArg(it) }.join(' ')} --verifications ${verifications.collect { quoteArg(it) }.join(' ')} --manifest-audit ${quoteArg(manifest_audit)} --settings ${quoteArg(settings)} --large-root ${quoteArg(params.cache + "/outputs/" + params.run_key + "/donors")} --out summary
    """
}

process REPORT {
    input:
    path summary
    path code
    path runtime
    output:
    path 'final'
    script:
    """
    python render_report.py --summary ${quoteArg(summary)} --out final
    """
}
