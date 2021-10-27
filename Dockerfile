FROM ubuntu:focal

MAINTAINER Luis Héctor Chávez <lhchavez@omegaup.com>

RUN ln -snf /usr/share/zoneinfo/Etc/UTC /etc/localtime && \
     echo Etc/UTC > /etc/timezone && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        clang-format \
        curl \
        git \
        locales \
        php-pear \
        php7.4-cli \
        php7.4-json \
        php7.4-mbstring \
        php7.4-xml \
        php7.4-zip \
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
        Jinja2==2.11.2 \
        pyparsing==2.4.7 \
        mypy==0.770 \
        pika-stubs==0.1.3 \
        pytest-stub==1.1.0 && \
    mkdir -p /.pylint.d && chown 1000:1000 /.pylint.d

# JavaScript support.
RUN git clone https://github.com/creationix/nvm.git /nvm && \
    (cd /nvm && git checkout `git describe --abbrev=0 --tags`) && \
    (. /nvm/nvm.sh && nvm install v12.18.2 ; nvm use --delete-prefix v12.18.2)
ENV PATH="/usr/bin/versions/node/v12.18.2/bin:${PATH}"
RUN npm install -g yarn && \
    yarn global add \
        @typescript-eslint/eslint-plugin \
        @typescript-eslint/parser \
        eslint \
        eslint_d \
        eslint-config-prettier \
        prettier-plugin-karel@1.0.2 \
        prettier@2.1.2 \
        stylelint-config-standard@21.0.0 \
        stylelint@13.12.0 \
        typescript

RUN useradd --uid 1000 --create-home ubuntu && \
    mkdir -p /.yarn /.cache && chown ubuntu:ubuntu /.yarn /.cache && \
    mkdir -p /src /hook_tools

# PHP support.
RUN curl --location \
        https://raw.githubusercontent.com/composer/getcomposer.org/76a7060ccb93902cd7576b67264ad91c8a2700e2/web/installer | \
      php -- --quiet --install-dir=/usr/bin --filename=composer
ENV PATH="/src/vendor/bin:/hook_tools/vendor/bin:${PATH}"

WORKDIR /src

ENV DOCKER=true
ENV LANG=en_US.UTF-8

ADD ./ /hook_tools
RUN (cd /hook_tools && composer install)

USER ubuntu
ENTRYPOINT ["/usr/bin/python3", "/hook_tools/lint.py"]
