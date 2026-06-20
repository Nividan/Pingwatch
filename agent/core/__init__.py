# core/ — shim package for the PingWatch remote agent.
#
# probes.py and radius_auth.py are verbatim copies of the server's modules;
# they import a handful of things from `core.*`. These tiny stand-ins satisfy
# those imports without dragging the whole server core along, which keeps the
# copies byte-identical to the canonical sources.
