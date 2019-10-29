#!/usr/bin/env python

import glob
import time
import re
import io
import base64
import sys
import os
import shutil
import json
import shelve
import hashlib
from importlib import import_module  # Python >= 2.7

try:
    import urllib2
except ImportError:
    import urllib.request as urllib2

# load settings file
from buildsettings import buildSettings

defaultBuild = None
if os.path.isfile('./localbuildsettings.py'):
    # load optional local settings file
    from localbuildsettings import buildSettings as localBuildSettings

    buildSettings.update(localBuildSettings)

    # load default build
    try:
        from localbuildsettings import defaultBuild
    except ImportError:
        pass

buildName = defaultBuild

# build name from command line
if len(sys.argv) == 2:  # argv[0] = program, argv[1] = buildname, len=2
    buildName = sys.argv[1]

if buildName is None or buildName not in buildSettings:
    print("Usage: build.py buildname")
    print(" available build names: %s" % ', '.join(buildSettings.keys()))
    sys.exit(1)

settings = buildSettings[buildName]

# set up vars used for replacements

utcTime = time.gmtime()
buildDate = time.strftime('%Y-%m-%d-%H%M%S', utcTime)
# userscripts have specific specifications for version numbers - the above date format doesn't match
dateTimeVersion = time.strftime('%Y%m%d.', utcTime) + time.strftime('%H%M%S', utcTime).lstrip('0')

# extract required values from the settings entry
resourceUrlBase = settings.get('resourceUrlBase')
distUrlBase = settings.get('distUrlBase')
buildMobile = settings.get('buildMobile')
gradleOptions = settings.get('gradleOptions', '')
gradleBuildFile = settings.get('gradleBuildFile', 'mobile/build.gradle')
pluginWrapper = import_module(settings.get('pluginWrapper','pluginwrapper'))
pluginWrapper.startUseStrict = pluginWrapper.start.replace("{\n", "{\n\"use strict\";\n", 1)

pluginMetaBlock = """// @updateURL      @@UPDATEURL@@
// @downloadURL    @@DOWNLOADURL@@"""


def readfile(fn):
    with io.open(fn, 'r', encoding='utf8') as f:
        return f.read()


def loaderRaw(var):
    fn = var.group(1)
    return readfile(fn)


def MultiLine(Str):
    return Str.replace('\\', '\\\\').replace('\n', '\\\n').replace('\'', '\\\'')


def loaderString(var):
    return MultiLine(loaderRaw(var))


def loaderCSS(var):
    Str =  re.sub('(?<=url\()["\']?([^)#]+?)["\']?(?=\))', loaderImage, loaderRaw(var))
    return MultiLine(Str)


def loaderImage(var):
    fn = var.group(1)
    _, ext = os.path.splitext(fn)
    return 'data:image/%s;base64,' % ('svg+xml' if ext == '.svg' else 'png') \
        + base64.b64encode(open(fn, 'rb').read()).decode('utf8')


def wrapInIIFE(fn):
    module = readfile(fn)
    name,_ = os.path.splitext(os.path.split(fn)[1])
    return '\n// *** module: ' + fn + ' ***\n' +\
        '(function () {\n' +\
        "var log = ulog('" + name + "');\n" +\
        module +\
        '\n})();\n'


def loadCode(ignore):
    return '\n\n;\n\n'.join(map(wrapInIIFE, sorted(glob.glob('code/*.js'))))


def extractUserScriptMeta(var):
    m = re.search(r"//[ \t]*==UserScript==\n.*?//[ \t]*==/UserScript==\n", var, re.MULTILINE | re.DOTALL)
    return m.group(0)


def doReplacements(script, updateUrl, downloadUrl, pluginName=None):
    script = re.sub('@@INJECTCODE@@', loadCode, script)

    script = script.replace('@@METAINFO@@', pluginMetaBlock)
    script = script.replace('@@PLUGINSTART@@', pluginWrapper.start)
    script = script.replace('@@PLUGINSTART-USE-STRICT@@', pluginWrapper.startUseStrict)
    script = script.replace('@@PLUGINEND@@',
        pluginWrapper.end if pluginName == 'total-conversion-build'
        else pluginWrapper.setup + pluginWrapper.end)

    script = re.sub('@@INCLUDERAW:([0-9a-zA-Z_./-]+)@@', loaderRaw, script)
    script = re.sub('@@INCLUDESTRING:([0-9a-zA-Z_./-]+)@@', loaderString, script)
    script = re.sub('@@INCLUDECSS:([0-9a-zA-Z_./-]+)@@', loaderCSS, script)
    script = re.sub('@@INCLUDEIMAGE:([0-9a-zA-Z_./-]+)@@', loaderImage, script)

    script = script.replace('@@BUILDDATE@@', buildDate)
    script = script.replace('@@DATETIMEVERSION@@', dateTimeVersion)

    if resourceUrlBase:
        script = script.replace('@@RESOURCEURLBASE@@', resourceUrlBase)
    else:
        if '@@RESOURCEURLBASE@@' in script:
            raise Exception("Error: '@@RESOURCEURLBASE@@' found in script, but no replacement defined")

    script = script.replace('@@BUILDNAME@@', buildName)

    script = script.replace('@@UPDATEURL@@', updateUrl)
    script = script.replace('@@DOWNLOADURL@@', downloadUrl)

    if (pluginName):
        script = script.replace('@@PLUGINNAME@@', pluginName)

    return script


def saveScriptAndMeta(script, ourDir, filename, oldDir=None):
    # TODO: if oldDir is set, compare files. if only data/time-based version strings are different
    # copy from there instead of saving a new file

    fn = os.path.join(outDir, filename)
    with io.open(fn, 'w', encoding='utf8') as f:
        f.write(script)

    metafn = fn.replace('.user.js', '.meta.js')
    if metafn != fn:
        with io.open(metafn, 'w', encoding='utf8') as f:
            meta = extractUserScriptMeta(script)
            f.write(meta)


outDir = os.path.join('build', buildName)

# create the build output

# first, delete any existing build - but keep it in a temporary folder for now
oldDir = None
if os.path.exists(outDir):
    oldDir = outDir + '~'
    if os.path.exists(oldDir):
        shutil.rmtree(oldDir)
    os.rename(outDir, oldDir)

# copy the 'dist' folder, if it exists
if os.path.exists('dist'):
    # this creates the target directory (and any missing parent dirs)
    # FIXME? replace with manual copy, and any .css and .js files are parsed for replacement tokens?
    shutil.copytree('dist', outDir)
else:
    # no 'dist' folder - so create an empty target folder
    os.makedirs(outDir)

# run any preBuild commands
for cmd in settings.get('preBuild', []):
    os.system(cmd)

# load main.js, parse, and create main total-conversion-build.user.js
main = readfile('main.js')

downloadUrl = distUrlBase and distUrlBase + '/total-conversion-build.user.js' or 'none'
updateUrl = distUrlBase and distUrlBase + '/total-conversion-build.meta.js' or 'none'
main = doReplacements(main, downloadUrl=downloadUrl, updateUrl=updateUrl, pluginName='total-conversion-build')

saveScriptAndMeta(main, outDir, 'total-conversion-build.user.js', oldDir)

with io.open(os.path.join(outDir, '.build-timestamp'), 'w') as f:
    f.write(u"" + time.strftime('%Y-%m-%d %H:%M:%S UTC', utcTime))

# for each plugin, load, parse, and save output
os.mkdir(os.path.join(outDir, 'plugins'))

for fn in glob.glob("plugins/*.user.js"):
    script = readfile(fn)

    downloadUrl = distUrlBase and distUrlBase + '/' + fn.replace("\\", "/") or 'none'
    updateUrl = distUrlBase and downloadUrl.replace('.user.js', '.meta.js') or 'none'
    pluginName = os.path.splitext(os.path.splitext(os.path.basename(fn))[0])[0]
    script = doReplacements(script, downloadUrl=downloadUrl, updateUrl=updateUrl, pluginName=pluginName)

    saveScriptAndMeta(script, outDir, fn, oldDir)

# if we're building mobile too
if buildMobile:
    if buildMobile not in ['debug', 'release', 'copyonly']:
        raise Exception("Error: buildMobile must be 'debug' or 'release' or 'copyonly'")

    # compile the user location script
    fn = "user-location.user.js"
    script = readfile("mobile/plugins/" + fn)
    downloadUrl = distUrlBase and distUrlBase + '/' + fn.replace("\\", "/") or 'none'
    updateUrl = distUrlBase and downloadUrl.replace('.user.js', '.meta.js') or 'none'
    script = doReplacements(script, downloadUrl=downloadUrl, updateUrl=updateUrl, pluginName='user-location')

    saveScriptAndMeta(script, outDir, fn)

    # copy the IITC script into the mobile folder. create the folder if needed
    try:
        os.makedirs("mobile/assets")
    except:
        pass
    shutil.copy(os.path.join(outDir, "total-conversion-build.user.js"), "mobile/assets/total-conversion-build.user.js")
    # copy the user location script into the mobile folder.
    shutil.copy(os.path.join(outDir, "user-location.user.js"), "mobile/assets/user-location.user.js")
    # also copy plugins
    try:
        shutil.rmtree("mobile/assets/plugins")
    except:
        pass
    ignore_patterns = settings.get('ignore_patterns') or []
    ignore_patterns.append('*.meta.js')
    shutil.copytree(os.path.join(outDir, "plugins"), "mobile/assets/plugins",
                    # do not include desktop-only plugins to mobile assets
                    ignore=shutil.ignore_patterns(*ignore_patterns))

    if buildMobile != 'copyonly':
        # now launch 'ant' to build the mobile project
        buildAction = "assemble" + buildMobile.capitalize()
        retcode = os.system("mobile/gradlew %s -b %s %s" % (gradleOptions, gradleBuildFile, buildAction))

        if retcode != 0:
            print("Error: mobile app failed to build. gradlew returned %d" % retcode)
            exit(1)  # ant may return 256, but python seems to allow only values <256
        else:
            shutil.copy("mobile/app/build/outputs/apk/%s/app-%s.apk" % (buildMobile, buildMobile),
                        os.path.join(outDir, "IITC_Mobile-%s.apk" % buildMobile))

# run any postBuild commands
for cmd in settings.get('postBuild', []):
    os.system(cmd)

# vim: ai si ts=4 sw=4 sts=4 et
