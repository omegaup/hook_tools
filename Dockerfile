FROM ubuntu:focal

MAINTAINER Luis Héctor Chávez <lhchavez@omegaup.com>

RUN ln -snf /usr/share/zoneinfo/Etc/UTC /etc/localtime && \
     echo Etc/UTC > /etc/timezone && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install --no-install-recommends -y \
        software-properties-common \
        && \
    add-apt-repository ppa:ondrej/php && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        clang-format \
        curl \
        git \
        locales \
        php8.0-cli \
        php8.0-mbstring \
        php8.0-xml \
        php8.0-zip \
        python3-pip \
        python3-setuptools \
        python3-six \
        unzip && \
    rm -rf /var/lib/apt/lists/* && \
    /usr/sbin/locale-gen en_US.UTF-8 && \
    /usr/sbin/update-locale LANG=en_US.UTF-8

# Python support.
RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install \
        pylint==2.5.3 \
        pycodestyle==2.6.0 \
        Jinja2==3.0.3 \
        types-requests==2.27.8 \
        pyparsing==2.4.7 \
        mypy==0.931 \
        pika-stubs==0.1.3 \
        pytest-stub==1.1.0 \
        omegaup==1.5.4 && \
    mkdir -p /.pylint.d && chown 1000:1000 /.pylint.d

# JavaScript support.
RUN git clone https://github.com/creationix/nvm.git /nvm --branch=v0.38.0 && \
    (. /nvm/nvm.sh && nvm install v12.18.2 ; nvm use --delete-prefix v12.18.2)
ENV PATH="/usr/bin/versions/node/v12.18.2/bin:${PATH}"
RUN npm install -g yarn && \
    yarn global add \
        @typescript-eslint/eslint-plugin@4.28.1 \
        @typescript-eslint/parser@4.28.1 \
        eslint@7.30.0 \
        eslint_d@10.1.3 \
        eslint-config-prettier@8.3.0 \
        prettier-plugin-karel@1.0.2 \
        prettier@2.1.2 \
        stylelint-config-standard@21.0.0 \
        stylelint@13.12.0 \
        typescript@4.3.5

RUN useradd --uid 1000 --create-home ubuntu && \
    mkdir -p /.yarn /.cache && chown ubuntu:ubuntu /.yarn /.cache && \
    mkdir -p /src /hook_tools

# PHP support.
RUN curl -sL https://getcomposer.org/download/2.1.14/composer.phar -o /usr/bin/composer && \
    chmod +x /usr/bin/composer
ENV PATH="/src/vendor/bin:/hook_tools/vendor/bin:${PATH}"

WORKDIR /src

ENV DOCKER=true
ENV LANG=en_US.UTF-8

ADD ./ /hook_tools
RUN (cd /hook_tools && composer install)

USER ubuntu
ENTRYPOINT ["/usr/bin/python3", "/hook_tools/lint.py"]
