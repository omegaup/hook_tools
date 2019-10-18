FROM ubuntu:bionic

MAINTAINER Luis Héctor Chávez <lhchavez@omegaup.com>

RUN ln -snf /usr/share/zoneinfo/Etc/UTC /etc/localtime && echo Etc/UTC > /etc/timezone
RUN apt-get update -y && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git python3-six python3-pip python3-setuptools php-pear curl locales
RUN /usr/sbin/locale-gen en_US.UTF-8 && /usr/sbin/update-locale LANG=en_US.UTF-8

# Python support.
RUN pip3 install --upgrade pip
RUN pip3 install pylint==2.4.1
RUN pip3 install pycodestyle==2.5.0
RUN pip3 install Jinja2==2.10.3
RUN pip3 install pyparsing==2.4.2

# PHP support.
RUN curl --location https://github.com/squizlabs/PHP_CodeSniffer/releases/download/3.5.0/phpcbf.phar -o /usr/bin/phpcbf && chmod 755 /usr/bin/phpcbf
RUN curl --location https://github.com/squizlabs/PHP_CodeSniffer/releases/download/3.5.0/phpcs.phar -o /usr/bin/phpcs && chmod 755 /usr/bin/phpcs

# JavaScript support.
RUN git clone https://github.com/creationix/nvm.git /nvm
RUN (cd /nvm && git checkout `git describe --abbrev=0 --tags`)
RUN (. /nvm/nvm.sh && nvm install 12.12.0)
ENV PATH="/bin/versions/node/v12.12.0/bin:${PATH}"
RUN npm install -g yarn
RUN yarn global add prettier@1.18.2

RUN mkdir -p /src
WORKDIR /src

ENV DOCKER=true
ENV LANG=en_US.UTF-8

RUN mkdir -p /hook_tools
ADD ./ /hook_tools

ENTRYPOINT ["/usr/bin/python3", "/hook_tools/lint.py"]
