"""Shared builders for the M4.2 tests."""
import copy
import json

from ecci import kicad_export as kx, symrec, writer
from m4_scenes import Scene, all_scenes, chain, crossing, t_junction, ambiguous_r  # noqa: F401
from test_rules import LIB

LIB_TEXT = open(LIB.path, encoding="utf-8").read()


def docs(scene: Scene, library=LIB):
    pi, cls, cl, built, res = scene.run() if library is LIB else _run_with(scene, library)
    conn = writer.loads(writer.dumps(built.graph, res, built=built, page=pi, clusters=cl, registry=cls.registry,
                                     source={"name": scene.name}))
    syms = symrec.loads(symrec.dumps(symrec.build_symbols(cl, pi, library, conn)), conn, library)
    return pi, conn, syms


def _run_with(scene, library):
    from test_rules import run
    return run(scene.content, symbols=library.symbols)


def rehash_conn(d):
    d = copy.deepcopy(d)
    d["content_sha256"] = writer.content_hash(d)
    return writer.canonical(d)


def rehash_syms(d):
    d = copy.deepcopy(d)
    d["content_sha256"] = symrec.content_hash(d)
    return symrec.canonical(d)


def export(scene, library=LIB, lib_text=LIB_TEXT):
    pi, conn, syms = docs(scene, library)
    sch, man = kx.export(conn, syms, lib_text)
    return pi, conn, syms, sch, man


def by_cluster_symbol(man, name):
    return [d for d in man["symbols"] if any(c["symbol"] == name and c["selected"] for c in d["candidates"])]


def j(x):
    return json.loads(json.dumps(x))
