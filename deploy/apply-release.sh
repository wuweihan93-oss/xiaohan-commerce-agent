#!/usr/bin/env bash
# 在服务器以镜像摘要部署；启动或代理检查失败时恢复上一份有效发布配置。
set -Eeuo pipefail
umask 077

die() { printf '部署失败：%s\n' "$*" >&2; exit 1; }
revision=${1:-}
deploy_path=${2:-}
release_source=${3:-}
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || die '提交版本格式不正确'
[[ "$deploy_path" == /* && "$deploy_path" != / ]] || die '部署目录必须是绝对路径'
[[ -n "$release_source" && -d "$release_source" ]] || die '部署包目录不存在'
[[ -f "$deploy_path/.env" ]] || die '服务器项目目录缺少 .env，请先完成首次配置'
[[ -d "$deploy_path/runtime/data" && -d "$deploy_path/knowledge" ]] || die '项目资料或数据目录不存在'

backend_image=$(<"$release_source/BACKEND_IMAGE")
frontend_image=$(<"$release_source/FRONTEND_IMAGE")
image_pattern='^[a-zA-Z0-9.-]+(:[0-9]{1,5})?(/[a-zA-Z0-9._-]+)+@sha256:[0-9a-f]{64}$'
[[ "$backend_image" =~ $image_pattern ]] || die '后端镜像地址或摘要无效'
[[ "$frontend_image" =~ $image_pattern ]] || die '前端镜像地址或摘要无效'
[[ "$(<"$release_source/REVISION")" == "$revision" ]] || die '部署包版本与目标版本不一致'
[[ -f "$release_source/compose.yaml" ]] || die '部署包缺少 compose.yaml'

state_dir="$deploy_path/.deploy"
releases_dir="$state_dir/releases"
release_dir="$releases_dir/$revision"
mkdir -p "$releases_dir" "$deploy_path/runtime/data"
[[ -d "$deploy_path/knowledge" ]] || die '项目资料目录不存在'
[[ ! -e "$release_dir" ]] || die "版本目录已存在：$revision"
staging_dir=$(mktemp -d "$releases_dir/.staging.XXXXXX")
release_ready=false
release_committed=false
cleanup() {
  rm -rf -- "$staging_dir"
  if [[ "$release_ready" == true && "$release_committed" != true ]]; then
    rm -rf -- "$release_dir"
  fi
}
trap cleanup EXIT
cp "$release_source/compose.yaml" "$staging_dir/compose.yaml"
cat > "$staging_dir/compose.override.yaml" <<EOF
services:
  backend:
    image: $backend_image
    build: null
  frontend:
    image: $frontend_image
    build: null
EOF
mv "$staging_dir" "$release_dir"
release_ready=true

compose=(docker compose --project-name xiaohan-commerce --project-directory "$deploy_path"
  -f "$release_dir/compose.yaml" -f "$release_dir/compose.override.yaml")
previous_revision=''
if [[ -f "$state_dir/current-revision" ]]; then
  previous_revision=$(<"$state_dir/current-revision")
  [[ "$previous_revision" =~ ^[0-9a-f]{40}$ ]] || die '当前版本记录格式异常'
fi

printf '拉取后端和前端镜像（版本 %s）\n' "$revision"
"${compose[@]}" pull
printf '启动新版本\n'
"${compose[@]}" up -d --remove-orphans

healthy=false
for attempt in {1..30}; do
  if "${compose[@]}" exec -T backend python -c \
      'from urllib.request import urlopen; urlopen("http://127.0.0.1:8020/api/health", timeout=5)' \
      >/dev/null 2>&1 \
    && "${compose[@]}" exec -T frontend wget -q -O /dev/null http://127.0.0.1/api/health \
      >/dev/null 2>&1; then
    healthy=true
    break
  fi
  sleep 2
done

if [[ "$healthy" != true ]]; then
  printf '新版本健康检查失败，开始恢复上一版本\n' >&2
  if [[ -n "$previous_revision" && -d "$releases_dir/$previous_revision" ]]; then
    rollback=(docker compose --project-name xiaohan-commerce --project-directory "$deploy_path"
      -f "$releases_dir/$previous_revision/compose.yaml"
      -f "$releases_dir/$previous_revision/compose.override.yaml")
    "${rollback[@]}" up -d || die '新版本检查失败，且恢复上一版本失败'
    printf '%s\n' "$previous_revision" > "$state_dir/current-revision"
  else
    die '新版本检查失败；没有记录可用的上一版本，请检查容器日志'
  fi
  die '上一版本已恢复，请检查 Docker 容器日志'
fi

printf '%s\n' "$revision" > "$state_dir/current-revision.tmp"
mv "$state_dir/current-revision.tmp" "$state_dir/current-revision"
release_committed=true
printf '部署成功：%s\n' "$revision"
