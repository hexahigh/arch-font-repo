#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_DIR="${ROOT_DIR}/repo"

PROD=1
DEREFERENCE=1
SITE_ID="${NETLIFY_SITE_ID:-}"
AUTH_TOKEN="${NETLIFY_AUTH_TOKEN:-}"
MESSAGE=""
EXTRA_ARGS=()

usage() {
	cat <<'EOF'
Usage: scripts/netlify-deploy.sh [options]

Deploy the local repo/ folder to Netlify using netlify-cli.

Options:
	--dir <path>         Directory to deploy (default: repo)
	--site-id <id>       Netlify site ID (or set NETLIFY_SITE_ID)
	--auth-token <token> Netlify auth token (or set NETLIFY_AUTH_TOKEN)
	--message <text>     Deploy message shown in Netlify UI
	--draft              Create a draft deploy (default is --prod)
	--prod               Force production deploy (default)
	--no-dereference     Keep symlinks as symlinks (default is dereference in-place)
	-h, --help           Show this help message

Examples:
	scripts/netlify-deploy.sh --prod
	scripts/netlify-deploy.sh --site-id "$NETLIFY_SITE_ID" --auth-token "$NETLIFY_AUTH_TOKEN"
EOF
}

while [[ $# -gt 0 ]]; do
	case "$1" in
		--dir)
			[[ $# -ge 2 ]] || { echo "Missing value for --dir"; exit 1; }
			DEPLOY_DIR="$2"
			shift 2
			;;
		--site-id)
			[[ $# -ge 2 ]] || { echo "Missing value for --site-id"; exit 1; }
			SITE_ID="$2"
			shift 2
			;;
		--auth-token)
			[[ $# -ge 2 ]] || { echo "Missing value for --auth-token"; exit 1; }
			AUTH_TOKEN="$2"
			shift 2
			;;
		--message)
			[[ $# -ge 2 ]] || { echo "Missing value for --message"; exit 1; }
			MESSAGE="$2"
			shift 2
			;;
		--draft)
			PROD=0
			shift
			;;
		--prod)
			PROD=1
			shift
			;;
		--no-dereference)
			DEREFERENCE=0
			shift
			;;
		-h|--help)
			usage
			exit 0
			;;
		--)
			shift
			EXTRA_ARGS+=("$@")
			break
			;;
		*)
			echo "Unknown option: $1"
			usage
			exit 1
			;;
	esac
done

if [[ ! -d "${DEPLOY_DIR}" ]]; then
	echo "Deploy directory not found: ${DEPLOY_DIR}"
	exit 1
fi

dereference_symlinks_in_place() {
	local dir="$1"
	local link=""
	local target=""
	local count=0

	while IFS= read -r -d '' link; do
		if ! target="$(readlink -f -- "${link}")"; then
			echo "Skipping broken symlink: ${link}"
			continue
		fi

		if [[ -d "${link}" ]]; then
			rm -- "${link}"
			mkdir -p -- "${link}"
			cp -a -- "${target}/." "${link}/"
		elif [[ -f "${link}" ]]; then
			rm -- "${link}"
			cp -a -- "${target}" "${link}"
		else
			echo "Skipping unsupported symlink target: ${link} -> ${target}"
			continue
		fi

		count=$((count + 1))
	done < <(find "${dir}" -depth -type l -print0)

	if [[ ${count} -gt 0 ]]; then
		echo "Dereferenced ${count} symlink(s) in-place under ${dir}"
	fi
}

if [[ ${DEREFERENCE} -eq 1 ]]; then
	dereference_symlinks_in_place "${DEPLOY_DIR}"
fi

# Check for npx equivalent. pnpx > pnpm dlx > npx > error
if command -v pnpx >/dev/null 2>&1; then
	NETLIFY_CMD=(pnpx --package=netlify-cli@latest netlify)
elif command -v pnpm >/dev/null 2>&1; then
	NETLIFY_CMD=(pnpm --package=netlify-cli@latest dlx netlify)
elif command -v npx >/dev/null 2>&1; then
	NETLIFY_CMD=(npx --yes --package=netlify-cli@latest netlify)
else
	echo "Could not find pnpx, pnpm, or npx. Install Node.js tooling first."
	exit 1
fi

deploy_cmd=("${NETLIFY_CMD[@]}" deploy --dir "${DEPLOY_DIR}")
if [[ ${PROD} -eq 1 ]]; then
	deploy_cmd+=(--prod)
fi
if [[ -n "${SITE_ID}" ]]; then
	deploy_cmd+=(--site "${SITE_ID}")
fi
if [[ -n "${MESSAGE}" ]]; then
	deploy_cmd+=(--message "${MESSAGE}")
fi
deploy_cmd+=("${EXTRA_ARGS[@]}")

if [[ -n "${AUTH_TOKEN}" ]]; then
	NETLIFY_AUTH_TOKEN="${AUTH_TOKEN}" "${deploy_cmd[@]}"
else
	"${deploy_cmd[@]}"
fi
