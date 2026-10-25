.PHONY: all linux windows test clean

all: linux

test:
	PYTHONPATH=src:tests .venv/bin/python -m unittest discover -s tests

linux:
	./build.sh linux

windows:
	./build.sh windows

clean:
	rm -rf build dist .build-venv
