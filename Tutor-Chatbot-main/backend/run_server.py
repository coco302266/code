"""一次性 bootstrap：chdir 到 backend、清掉代理、起 uvicorn。用完即删。"""
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(k, None)

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
