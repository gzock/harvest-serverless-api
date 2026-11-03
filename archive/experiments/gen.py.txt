import string

prev = ""

print("parent,type,name")

for c in string.ascii_lowercase:
  for i in range(6):
    name = c * 3 + "_" + str(i)
    if i < 5:
      print("%s,place,%s" % (prev, name))
    for j in range(10):
      print("%s,target,target_%s_%s" % (prev, name, str(j)))
    prev = name
  else:
    prev = ""
