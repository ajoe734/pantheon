import os
import json
import requests
import re

def get_env_var(key):
    return os.environ.get(key)

def get_url_content(url):
    response = requests.get(url)
    if response.status_code != 200:
        return None
    return response.text

def verify_strict_publish(deployment_url, env_getter=get_env_var, url_getter=get_url_content):
    """
    Verifies that the strict publish environment settings are applied
    and the deployed bundle does not contain mock/fallback assets.
    """
    # 1. Check Environment Variables
    required_env = {
        "VITE_BFF_MODE": "live",
        "VITE_BFF_FALLBACK": "strict",
        "VITE_BFF_REAL_WRITES": "false"
    }
    
    missing_flags = []
    for key, value in required_env.items():
        if env_getter(key) != value:
            missing_flags.append(key)
            
    strict_env_confirmed = len(missing_flags) == 0
    
    # 2. Probe Bundle
    try:
        content = url_getter(deployment_url)
        if content is None:
            return {"strict_env_confirmed": strict_env_confirmed, "missing_flags": missing_flags, "probe_failed": True}
        
        # Simple probe for mock/fallback references
        if re.search(r'/mocks/|seed\.', content):
            return {"strict_env_confirmed": strict_env_confirmed, "missing_flags": missing_flags, "probe_failed": False, "contains_mocks": True}
            
    except Exception:
        return {"strict_env_confirmed": strict_env_confirmed, "missing_flags": missing_flags, "probe_failed": True}
        
    return {"strict_env_confirmed": strict_env_confirmed, "missing_flags": missing_flags, "probe_failed": False, "contains_mocks": False}

if __name__ == "__main__":
    url = os.environ.get("DEPLOYMENT_URL", "http://localhost:3000")
    result = verify_strict_publish(url)
    print(json.dumps(result, indent=2))
