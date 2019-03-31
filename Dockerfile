FROM ubuntu:xenial

MAINTAINER Luis Héctor Chávez <lhchavez@omegaup.com>

RUN apt-get update -y && apt-get install -y git clang-format-3.7 python-pip python-six python3-six python3-pip php-pear curl
RUN pip3 install --upgrade pip
RUN pip3 install pylint==2.3.1
RUN pip3 install pycodestyle==2.5.0
RUN pip3 install Jinja2==2.10
RUN pip3 install pyparsing==2.3.1
RUN pip install --user https://github.com/google/closure-linter/zipball/master
RUN curl --location https://github.com/squizlabs/PHP_CodeSniffer/releases/download/3.4.0/phpcbf.phar -o /usr/bin/phpcbf && chmod 755 /usr/bin/phpcbf
RUN git clone https://github.com/creationix/nvm.git /nvm
RUN (cd /nvm && git checkout `git describe --abbrev=0 --tags`)
RUN (. /nvm/nvm.sh && nvm install 11.12.0)
ENV PATH="/bin/versions/node/v11.12.0/bin:${PATH}"
RUN npm install -g yarn

RUN mkdir -p /src
WORKDIR /src

ENV DOCKER=true

RUN mkdir -p /hook_tools
ADD ./ /hook_tools

ENTRYPOINT ["/usr/bin/python3", "/hook_tools/lint.py"]
