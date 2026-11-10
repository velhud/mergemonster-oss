EXT_GROUPS = {
    "CODE": {
        # Common languages
        "py",
        "js",
        "jsx",
        "ts",
        "tsx",
        "go",
        "rs",
        "c",
        "cpp",
        "swift",
        "java",
        # iOS/Apple and C-family extras
        "m",       # Objective-C
        "mm",      # Objective-C++
        "h",       # C/ObjC header
        "hh",      # C++/ObjC++ header
        "hpp",     # C++ header
        "cxx",     # C++ source
        "mpp",     # C++ module partition (rare but text)
        "metal",   # Metal shading language
        "modulemap",
        "xcconfig",
    },
    # Treat Xcode interface/resources as markup-like text files
    "MARKUP": {"html", "xml", "svg", "md", "rst", "storyboard", "xib"},
    # Data/config-like text used in Apple projects as well
    "DATA": {
        "json",
        "yml",
        "yaml",
        "toml",
        "sql",
        "csv",
        # iOS/macOS project text files
        "plist",
        "pbxproj",
        "strings",
        "entitlements",
        # Xcode workspace/schemes
        "xcworkspacedata",
        "xcscheme",
        "xcuserstate",
        "xcsettings",
    },
    "BINARY": {
        "png",
        "jpg",
        "jpeg",
        "gif",
        "ico",
        "webp",
        "mp4",
        "mp3",
        "wav",
        "ttf",
        "otf",
        "woff",
        "woff2",
        "eot",
        "pkl",
        "pickle",
    },
}

__all__ = ["EXT_GROUPS"]
