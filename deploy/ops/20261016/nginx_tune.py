import sys
p = sys.argv[1]; s = open(p).read()
old = "    location = / {\n        return 302 /studio;\n    }\n"
assert s.count(old) == 1 and "location = /tune" not in s
s = s.replace(old, old + "\n    location = /tune {\n        return 302 /studio/tune;\n    }\n")
a = s.index("    location /studio {"); b = s.index("    }\n", a)
blk = s[a:b]
assert blk.count("proxy_read_timeout 120s;") == 1
s = s[:a] + blk.replace("proxy_read_timeout 120s;", "proxy_read_timeout 300s;") + s[b:]
open(p, "w").write(s)
print("edited")
