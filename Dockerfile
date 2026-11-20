ARG VERSION=dev

FROM ubuntu:jammy as build

MAINTAINER Luis Héctor Chávez <lhchavez@omegaup.com>

RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        git \
        python3-pip \
        python3.10-venv \
        && \
    rm -rf /var/lib/apt/lists/*

# Python support.
RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install \
        build \
        setuptools

ARG VERSION $VERSION
ADD ./ /hook_tools
RUN if [ "${VERSION}" = "dev" ]; then \
      cd /hook_tools && \
        python3 -m build; \
    else \
      mkdir /hook_tools/dist/ && \
      touch /hook_tools/dist/empty.whl; \
    fi

FROM ubuntu:jammy

MAINTAINER Luis Héctor Chávez <lhchavez@omegaup.com>

RUN ln -snf /usr/share/zoneinfo/Etc/UTC /etc/localtime && \
     echo Etc/UTC > /etc/timezone && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        clang-format \
        curl \
        git \
        locales \
        php8.1-cli \
        php8.1-mbstring \
        php8.1-xml \
        php8.1-zip \
        python3-pip \
        python3.10-venv \
        unzip && \
    rm -rf /var/lib/apt/lists/* && \
    /usr/sbin/locale-gen en_US.UTF-8 && \
    /usr/sbin/update-locale LANG=en_US.UTF-8

# Python support.
ADD requirements.txt requirements.test.txt ./
RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install -r requirements.txt -r requirements.test.txt && \
    mkdir -p /.pylint.d && chown 1000:1000 /.pylint.d

# Node 22 runs Oxlint and Oxfmt. Node 14 cannot load their ESM bins.
ENV NVM_DIR=/nvm
RUN git clone --depth 1 --branch v0.41.0 https://github.com/nvm-sh/nvm.git /nvm && \
    . /nvm/nvm.sh && nvm install 22.23.5
ENV PATH="/nvm/versions/node/v22.23.5/bin:${PATH}"
RUN npm install -g \
        oxlint@1.94.0 \
        oxfmt@0.79.0 \
        stylelint-config-standard@21.0.0 \
        stylelint@13.12.0 \
    && node --version && oxlint --version && oxfmt --version

RUN useradd --uid 1000 --create-home ubuntu && \
    mkdir -p /.cache && chown ubuntu:ubuntu /.cache && \
    mkdir -p /src /hook_tools

# PHP support.
RUN curl -sL https://getcomposer.org/download/2.1.14/composer.phar -o /usr/bin/composer && \
    chmod +x /usr/bin/composer
ENV PATH="/src/vendor/bin:/hook_tools/vendor/bin:${PATH}"

WORKDIR /src

ENV DOCKER=true
ENV LANG=en_US.UTF-8

COPY --from=build /hook_tools/dist/*.whl /tmp/
ARG VERSION $VERSION
RUN if [ "${VERSION}" = "dev" ]; then \
      python3 -m pip install /tmp/*.whl && rm /tmp/*.whl; \
    else \
      python3 -m pip install "omegaup-hook-tools==$(echo "${VERSION}" | sed -e 's/^v//')"; \
    fi

ADD ./ /hook_tools
RUN (cd /hook_tools && composer install)

# Allow non-Linux machines to use `git` everywhere in the filesystem, since all
# the virtualization techniques will cause the filesystems to be mounted with
# unexpected permissions that make git bail out.
RUN printf '[safe]\n\tdirectory = *\n' >> /etc/gitconfig

USER ubuntu
ENTRYPOINT ["python3", "-m", "omegaup_hook_tools"]
