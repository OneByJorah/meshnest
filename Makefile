.PHONY: test lint deb clean

test:
	python3 -m unittest discover -s tests -v

lint:
	shellcheck -x install.sh bin/j1-mesh lib/*.sh packaging/*.sh

deb:
	./packaging/build-deb.sh

clean:
	rm -rf dist
