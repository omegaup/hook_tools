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

# JavaScript support.
RUN git clone https://github.com/creationix/nvm.git /nvm --branch=v0.38.0 && \
    (. /nvm/nvm.sh && nvm install v14.17.3 ; nvm use --delete-prefix v14.17.3)
ENV PATH="/usr/bin/versions/node/v14.17.3/bin:${PATH}"
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

COPY --from=build /hook_tools/dist/*.whl /tmp/
ARG VERSION $VERSION
RUN if [ "${VERSION}" = "dev" ]; then \
      python3 -m pip install /tmp/*.whl && rm /tmp/*.whl; \
    else \
      python3 -m pip install "omegaup-hook-tools==${VERSION}"; \
    fi

ADD ./ /hook_tools
RUN (cd /hook_tools && composer install)

# Allow non-Linux machines to use `git` everywhere in the filesystem, since all
# the virtualization techniques will cause the filesystems to be mounted with
# unexpected permissions that make git bail out.
RUN printf '[safe]\n\tdirectory = *\n' >> /etc/gitconfig

USER ubuntu
ENTRYPOINT ["python3", "-m", "omegaup_hook_tools"]
