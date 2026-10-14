import json
import urllib.request

REPO = "LifeArchiveProject/WeChatDataAnalysis"


def g(u):
    r = urllib.request.Request(u, headers={"User-Agent": "dsh", "Accept": "application/vnd.github+json"})
    return json.loads(urllib.request.urlopen(r, timeout=60).read().decode("utf-8"))


# Compare every src/**/*.py blob sha between v2.9.0 and main, and between main and the
# commit that the installed build's date corresponds to.
t_main = g(f"https://api.github.com/repos/{REPO}/git/trees/main?recursive=1")
t_tag = g(f"https://api.github.com/repos/{REPO}/git/trees/v2.9.0?recursive=1")

main_py = {n["path"]: n["sha"] for n in t_main["tree"] if n["type"] == "blob" and n["path"].startswith("src/") and n["path"].endswith(".py")}
tag_py = {n["path"]: n["sha"] for n in t_tag["tree"] if n["type"] == "blob" and n["path"].startswith("src/") and n["path"].endswith(".py")}

same = [p for p in main_py if tag_py.get(p) == main_py[p]]
only_main = [p for p in main_py if p not in tag_py]
only_tag = [p for p in tag_py if p not in main_py]
diff = [p for p in main_py if p in tag_py and tag_py[p] != main_py[p]]

print(f"src/**/*.py  main={len(main_py)}  v2.9.0={len(tag_py)}")
print(f"  identical : {len(same)}")
print(f"  differ    : {len(diff)} {diff[:10]}")
print(f"  only main : {len(only_main)} {only_main[:10]}")
print(f"  only tag  : {len(only_tag)} {only_tag[:10]}")

# Also: which commit is main HEAD, and does it equal the tag's commit?
head = g(f"https://api.github.com/repos/{REPO}/commits/main")
tag = g(f"https://api.github.com/repos/{REPO}/git/ref/tags/v2.9.0")
print("\nmain HEAD :", head["sha"], head["commit"]["committer"]["date"], head["commit"]["message"].splitlines()[0][:60])
print("v2.9.0 tag object:", tag["object"]["sha"], tag["object"]["type"])
if tag["object"]["type"] == "tag":
    tt = g(f"https://api.github.com/repos/{REPO}/git/tags/{tag['object']['sha']}")
    print("v2.9.0 -> commit:", tt["object"]["sha"], tt["tagger"]["date"])
