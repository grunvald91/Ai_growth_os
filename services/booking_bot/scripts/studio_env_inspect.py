"""Names only (values masked): does compose resolve the sign-in settings for the studio api container?"""
import json, re, subprocess
c = json.loads(subprocess.check_output(["docker", "inspect", "ai-growth-os-api-1"], text=True))[0]
labels = c["Config"]["Labels"]
files = labels["com.docker.compose.project.config_files"].split(",")
cwd = labels["com.docker.compose.project.working_dir"]
overlay = files[-1]
print("overlay=" + overlay)
doc = json.load(open(overlay))
print("overlay_api_env=" + json.dumps({k: (v if v.startswith("${") else "<value>") for k, v in doc["services"]["api"]["environment"].items()}, ensure_ascii=False))
cmd = ["docker", "compose", "--project-name", "ai-growth-os", "--env-file", "/opt/ai_growth_os/.env"]
for f in files:
    cmd += ["-f", f]
out = subprocess.run(cmd + ["config", "--format", "json"], cwd=cwd, capture_output=True, text=True)
print("compose_config_exit=" + str(out.returncode) + " err=" + out.stderr.strip()[:200].replace("\n", " "))
if out.returncode == 0:
    env = json.loads(out.stdout)["services"]["api"]["environment"]
    for key in ("SERVGO_SSO_SECRET", "SERVGO_INTERNAL_KEY", "RELEASE_SHA"):
        value = env.get(key)
        print(f"resolved {key}: " + ("MISSING" if key not in env else "null" if value is None else f"len={len(str(value))}"))
text = open("/opt/ai_growth_os/.env", encoding="utf-8").read().splitlines()
for line in text:
    if line.startswith(("SERVGO", "export SERVGO")) or "SERVGO" in line.split("=")[0]:
        name, _, value = line.partition("=")
        print(f"dotenv {name!r}: len={len(value)} quoted={value[:1] in '\"\''} ")
print("dotenv_has_trailing_cr=" + str(any(l.endswith('\r') for l in open('/opt/ai_growth_os/.env', newline='').read().split('\n') if 'SERVGO' in l)))
