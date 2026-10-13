"""Build the site's page (dist/index.html) from the dashboard (site/dashboard.html): no snapshot baked in;
the page reads the desk's numbers from /data/ once signed in."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
page = open(os.path.join(HERE, "dashboard.html"), encoding="utf-8").read()
assert page.count("__SNAPSHOT__") == 1, "dashboard.html must hold one __SNAPSHOT__ placeholder"
os.makedirs(os.path.join(HERE, "dist"), exist_ok=True)
with open(os.path.join(HERE, "dist", "index.html"), "w", encoding="utf-8") as f:
    f.write(page.replace("__SNAPSHOT__", "null"))
print("dist/index.html", len(page))
