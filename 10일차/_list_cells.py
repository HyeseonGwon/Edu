import json
with open(r"c:\Users\Admin\Desktop\실습\10일차\머신러닝 Day 3. 미니프로젝트.ipynb", encoding="utf-8") as f:
    nb = json.load(f)
for i, c in enumerate(nb["cells"]):
    src = "".join(c.get("source", []))[:80].replace("\n", " ")
    print(i, c["cell_type"], repr(src))
