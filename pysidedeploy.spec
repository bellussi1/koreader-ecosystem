[app]
title = KOReaderEcosystem
project_dir = .
icon = assets/KOReaderEcosystem.ico
input_file = build\windows\KOReaderEcosystem.py
project_file = pyproject.toml
exec_directory = build/windows

[python]
packages = Nuitka==4.3.1

[qt]
modules = Core,Gui,Network,Widgets

[nuitka]
mode = standalone
extra_args = --quiet --noinclude-qt-translations --windows-console-mode=disable --include-distribution-metadata=keyring --zig --assume-yes-for-downloads
