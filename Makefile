.PHONY: all linux windows test gui clean

all: linux

test:
	PYTHONPATH=src:tests .venv/bin/python -m unittest discover -s tests

gui:
	PYTHONPATH=src .venv/bin/python -m dokkan.gui

linux:
	./build.sh linux

windows:
	./build.sh windows

clean:
	rm -rf build dist .build-venv
