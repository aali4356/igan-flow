#!/usr/bin/env bash
IGAN_CACHE=${IGAN_CACHE:-"$HOME/.cache/igan-nextflow"}
export IGAN_CACHE
if [[ "$(uname -s)" == Darwin ]]; then
  export JAVA_HOME="$IGAN_CACHE/tools/java/Contents/Home"
else
  export JAVA_HOME="$IGAN_CACHE/tools/java"
fi
export PATH="$IGAN_CACHE/tools/python/bin:$IGAN_CACHE/tools:$JAVA_HOME/bin:$PATH"
export NXF_HOME="$IGAN_CACHE/nextflow-home"
export NXF_VER=26.04.6 NXF_OFFLINE=true NXF_DISABLE_CHECK_LATEST=true
export NXF_OPTS='-Xms256m -Xmx2g -XX:ActiveProcessorCount=2'
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 VECLIB_MAXIMUM_THREADS=2 NUMEXPR_NUM_THREADS=2
export MPLBACKEND=Agg MPLCONFIGDIR="$IGAN_CACHE/matplotlib" PYTHONDONTWRITEBYTECODE=1
export PLAYWRIGHT_BROWSERS_PATH="$IGAN_CACHE/tools/browsers"
