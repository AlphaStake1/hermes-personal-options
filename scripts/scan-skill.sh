#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/scan-skill.sh [--semantic] [--output-dir DIR] <skill-path-or-url>

Runs NVIDIA SkillSpector before any skill is installed or enabled.

Default mode is static-only:
  skillspector scan <target> --no-llm

Use --semantic for LLM-backed semantic analysis when a skill is non-trivial,
external, executable, permission-expanding, or security-sensitive. Configure
SkillSpector's provider and API key in the shell or a protected env file before
using --semantic. Do not put scanner credentials in this repo.
USAGE
}

semantic=false
output_dir=".skillspector-reports"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --semantic)
      semantic=true
      shift
      ;;
    --output-dir)
      if [[ $# -lt 2 ]]; then
        echo "error: --output-dir requires a directory" >&2
        usage >&2
        exit 2
      fi
      output_dir="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      break
      ;;
  esac
done

if [[ $# -ne 1 ]]; then
  echo "error: expected exactly one skill path or URL" >&2
  usage >&2
  exit 2
fi

target="$1"

if ! command -v skillspector >/dev/null 2>&1; then
  echo "error: skillspector is not installed or not on PATH" >&2
  echo "install from official NVIDIA docs: https://docs.nvidia.com/skills/scanning-agent-skills" >&2
  exit 127
fi

mkdir -p "$output_dir"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
safe_name="$(printf '%s' "$target" | tr -cs '[:alnum:]_.-' '_' | sed 's/^_//; s/_$//')"
if [[ -z "$safe_name" ]]; then
  safe_name="skill"
fi

json_report="$output_dir/${timestamp}_${safe_name}.json"
markdown_report="$output_dir/${timestamp}_${safe_name}.md"

scan_args=(scan "$target")
if [[ "$semantic" == false ]]; then
  scan_args+=(--no-llm)
fi

if [[ "$semantic" == true ]]; then
  echo "Running SkillSpector semantic scan target=$target"
else
  echo "Running SkillSpector static scan target=$target"
fi
skillspector "${scan_args[@]}" --format json --output "$json_report"
skillspector "${scan_args[@]}" --format markdown --output "$markdown_report"

echo "SkillSpector reports:"
echo "  JSON: $json_report"
echo "  Markdown: $markdown_report"
echo
echo "Do not install or enable the skill until findings are reviewed and any"
echo "high/critical risks, hidden instructions, tool poisoning, credential"
echo "access, or description-behavior mismatches are fixed or explicitly rejected."
