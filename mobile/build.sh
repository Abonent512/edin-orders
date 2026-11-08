#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
: "${ANDROID_SDK_ROOT:?Укажите ANDROID_SDK_ROOT с установленными Android 35 и build-tools 35.0.0}"
SDK="$ANDROID_SDK_ROOT"
BT="$SDK/build-tools/35.0.0"
JAR="$SDK/platforms/android-35/android.jar"
mkdir -p build/classes build/dex build/compiled
"$BT/aapt2" compile --dir res -o build/compiled
"$BT/aapt2" link -o build/base.apk -I "$JAR" --manifest AndroidManifest.xml --min-sdk-version 26 --target-sdk-version 35 build/compiled/*
javac -source 8 -target 8 -classpath "$JAR" -d build/classes src/ru/edin/orders/MainActivity.java
find build/classes -name '*.class' -print0 | xargs -0 "$BT/d8" --lib "$JAR" --min-api 26 --output build/dex
cp build/base.apk build/unsigned.apk
(cd build/dex && zip -q ../unsigned.apk classes.dex)
"$BT/zipalign" -f 4 build/unsigned.apk build/aligned.apk
if [ -z "${EDIN_KEYSTORE:-}" ]; then
    # Development signing only. Production owners must keep their own keystore.
    if [ ! -f build/demo.keystore ]; then
      keytool -genkeypair -keystore build/demo.keystore -storepass android -keypass android -alias edin-demo -keyalg RSA -keysize 2048 -validity 3650 -dname 'CN=EDIN Development, O=Demo, C=RU'
    fi
    "$BT/apksigner" sign --ks build/demo.keystore --ks-key-alias edin-demo --ks-pass pass:android --key-pass pass:android --out build/edin-android-demo.apk build/aligned.apk
else
    "$BT/apksigner" sign --ks "$EDIN_KEYSTORE" --out build/edin-android.apk build/aligned.apk
fi
"$BT/apksigner" verify --verbose build/edin-android*.apk
