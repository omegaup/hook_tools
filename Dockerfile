FROM ubuntu:xenial

MAINTAINER Luis Héctor Chávez <lhchavez@omegaup.com>

RUN apt-get update -y && apt-get install -y git clang-format-3.7 python-pip python-six python3-six python3-pep8 pylint3 nodejs php-pear curl
RUN pip install --user https://github.com/google/closure-linter/zipball/master
RUN pear install pear/PHP_CodeSniffer-2.9.1
RUN git clone https://github.com/creationix/nvm.git /nvm
RUN (cd /nvm && git checkout `git describe --abbrev=0 --tags`)
RUN (. /nvm/nvm.sh && nvm install 6.9.1)
ENV PATH="/bin/versions/node/v6.9.1/bin:${PATH}"
RUN npm install -g yarn

RUN mkdir -p /src
WORKDIR /src

RUN mkdir -p /hook_tools
RUN git clone https://github.com/omegaup/hook_tools.git /hook_tools

ENTRYPOINT ["/usr/bin/python3", "/hook_tools/lint.py"]
