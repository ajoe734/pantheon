#!/usr/bin/env bash
# Provision the real worker sandbox (bubblewrap + AppArmor upstream profile) on a
# GitHub-hosted Ubuntu runner. Fail closed. Exit codes:
#   10 package metadata (apt-get update) failed on primary and official fallback
#   11 package install / inventory failed
#   12 AppArmor profile load or real bwrap capability probe failed
#   13 unsupported distribution (fallback refused; primary failure is kept)
# Test seams (env): SANDBOX_SUDO, SANDBOX_APT_GET, SANDBOX_DPKG_QUERY,
#   SANDBOX_OS_RELEASE, SANDBOX_PROFILE, SANDBOX_APPARMOR_PARSER, SANDBOX_BWRAP.
set -uo pipefail

SUDO="${SANDBOX_SUDO-sudo}"
APT_GET="${SANDBOX_APT_GET:-apt-get}"
DPKG_QUERY="${SANDBOX_DPKG_QUERY:-dpkg-query}"
OS_RELEASE="${SANDBOX_OS_RELEASE:-/etc/os-release}"
PROFILE="${SANDBOX_PROFILE:-/usr/share/apparmor/extra-profiles/bwrap-userns-restrict}"
APPARMOR_PARSER="${SANDBOX_APPARMOR_PARSER:-apparmor_parser}"
BWRAP="${SANDBOX_BWRAP:-/usr/bin/bwrap}"
UPDATE_TIMEOUT="${SANDBOX_UPDATE_TIMEOUT:-45}"
INSTALL_TIMEOUT="${SANDBOX_INSTALL_TIMEOUT:-60}"
GETENT="${SANDBOX_GETENT:-getent}"
HOSTS_FILE="${SANDBOX_HOSTS:-/etc/hosts}"
RESOLV_FILE="${SANDBOX_RESOLV:-/etc/resolv.conf}"
DIAG_HOSTS=(archive.ubuntu.com security.ubuntu.com github.com)
KEYRING="${SANDBOX_KEYRING:-/usr/share/keyrings/ubuntu-archive-keyring.gpg}"

# Bounded apt options; package signature verification stays fully enabled.
BOUND=(-o Acquire::Retries=1 -o Acquire::http::Timeout=15 -o Acquire::https::Timeout=15)
PACKAGES=(bubblewrap apparmor-profiles)

run_apt() { # run_apt <timeout-seconds> <args...>
  local t="$1"; shift
  # shellcheck disable=SC2086
  timeout --kill-after=5s "${t}s" $SUDO $APT_GET "${BOUND[@]}" "$@"
}

try_primary() {
  run_apt "$UPDATE_TIMEOUT" update; local rc=$?
  if [ "$rc" -ne 0 ]; then echo "::warning::primary apt update failed (exit $rc)"; return 10; fi
  run_apt "$INSTALL_TIMEOUT" install --yes --no-install-recommends "${PACKAGES[@]}"; rc=$?
  if [ "$rc" -ne 0 ]; then echo "::warning::primary apt install failed (exit $rc)"; return 11; fi
  return 0
}

try_fallback() {
  local id codename tmp
  id="$(. "$OS_RELEASE" 2>/dev/null; echo "${ID:-}")"
  codename="$(. "$OS_RELEASE" 2>/dev/null; echo "${VERSION_CODENAME:-}")"
  if [ "$id" != ubuntu ] || [ -z "$codename" ]; then
    echo "::error::official fallback refused: unsupported distribution (ID=$id codename=$codename)"
    return 13
  fi
  tmp="$(mktemp)"
  {
    echo "deb [signed-by=$KEYRING] http://archive.ubuntu.com/ubuntu $codename main universe"
    echo "deb [signed-by=$KEYRING] http://archive.ubuntu.com/ubuntu $codename-updates main universe"
    echo "deb [signed-by=$KEYRING] http://security.ubuntu.com/ubuntu $codename-security main universe"
  } >"$tmp"
  local opts=(-o "Dir::Etc::sourcelist=$tmp" -o Dir::Etc::sourceparts=/dev/null)
  echo "::notice::falling back to official Ubuntu archive sources ($codename), signed-by $KEYRING"
  local rc=0
  # shellcheck disable=SC2086
  timeout --kill-after=5s "${UPDATE_TIMEOUT}s" $SUDO $APT_GET "${BOUND[@]}" "${opts[@]}" update; rc=$?
  if [ "$rc" -ne 0 ]; then rm -f "$tmp"; echo "::error::fallback apt update failed (exit $rc)"; return 10; fi
  # shellcheck disable=SC2086
  timeout --kill-after=5s "${INSTALL_TIMEOUT}s" $SUDO $APT_GET "${BOUND[@]}" "${opts[@]}" \
    install --yes --no-install-recommends "${PACKAGES[@]}"; rc=$?
  rm -f "$tmp"
  if [ "$rc" -ne 0 ]; then echo "::error::fallback apt install failed (exit $rc)"; return 11; fi
  return 0
}

# Failure-only, bounded DNS discriminators (exits/counts only; no config content,
# no new resolver, no change to apt verification). Never affects the exit code.
dns_diag() {
  local h rc n
  echo "::group::dns diagnostics (counts only)"
  for h in "${DIAG_HOSTS[@]}"; do
    n="$(timeout 5s "$GETENT" -s dns hosts "$h" 2>/dev/null | wc -l)"; rc=$?  # pipefail carries getent/timeout status
    echo "diag getent-dns host=$h exit=$rc answer_lines=$n"
    n="$(RES_OPTIONS='use-vc attempts=1 timeout=3' timeout 8s "$GETENT" -s dns hosts "$h" 2>/dev/null | wc -l)"; rc=$?  # pipefail carries getent/timeout status
    echo "diag getent-dns-tcp host=$h exit=$rc answer_lines=$n"
    n="$(grep -c -F -w "$h" "$HOSTS_FILE" 2>/dev/null)"
    echo "diag hosts-file-entries host=$h count=${n:-0}"
  done
  if [ -r "$RESOLV_FILE" ]; then
    echo "diag resolv-conf readable=yes nameserver_count=$(grep -c '^nameserver' "$RESOLV_FILE" 2>/dev/null)"
  else
    echo "diag resolv-conf readable=no"
  fi
  echo "::endgroup::"
}

try_primary; prc=$?
if [ "$prc" -ne 0 ]; then
  try_fallback; frc=$?
  if [ "$frc" -ne 0 ]; then
    echo "::error::package provisioning failed: primary=$prc fallback=$frc"
    dns_diag
    # Metadata failures stay distinguishable from install failures.
    [ "$frc" -eq 13 ] && exit "$prc"
    exit "$frc"
  fi
fi

"$DPKG_QUERY" --show --showformat='${Package} ${Version}\n' bubblewrap apparmor apparmor-profiles || exit 11

# Ubuntu supplies this per-binary profile for restricted userns hosts.
# Load it unchanged; retain host-wide restrictions and child capability denial.
sha256sum "$PROFILE" || exit 12
# shellcheck disable=SC2086
$SUDO $APPARMOR_PARSER --replace "$PROFILE" || exit 12
if [ -z "${SANDBOX_BWRAP:-}" ]; then
  test "$(command -v bwrap)" = /usr/bin/bwrap || { echo "::error::bwrap is not /usr/bin/bwrap"; exit 12; }
fi
"$BWRAP" --version || exit 12
timeout 5s "$BWRAP" --die-with-parent --unshare-pid \
  --ro-bind / / --dev-bind /dev /dev --proc /proc -- /bin/true || { echo "::error::bwrap capability probe failed"; exit 12; }
echo "Real worker sandbox capability probe passed (exit 0)"
