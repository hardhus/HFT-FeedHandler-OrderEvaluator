"""Make pytest's JUnit XML report readable: indented, sorted, without xdist group suffixes."""

import xml.etree.ElementTree as ET
from pathlib import Path


def tidy_junit(path: Path) -> None:
    """Rewrite the report in place.

    - one element per line, indented;
    - test cases sorted by class and name;
    - the "@<group>" suffix that pytest-xdist --dist loadgroup appends to test ids removed.
    """
    tree = ET.parse(path)
    for suite in tree.getroot().iter("testsuite"):
        cases = suite.findall("testcase")
        for case in cases:
            for key in ("name", "classname"):
                if key in case.attrib:
                    case.set(key, case.get(key, "").split("@", 1)[0])
            suite.remove(case)
        cases.sort(key=lambda c: (c.get("classname", ""), c.get("name", "")))
        suite.extend(cases)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n")
