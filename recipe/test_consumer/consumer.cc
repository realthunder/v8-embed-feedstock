// Embeds the package the way an application does, and checks the two things
// that make it worth packaging.
//
// First that the library is complete: the default platform, the startup
// snapshot and the engine are all inside the one shared object, so this file
// links nothing else and still gets an isolate that runs a script.  A build
// where the snapshot or libplatform were dropped at link time passes every
// static check and then dies here on the first context.
//
// Second that the context is bare.  A fresh V8 global has ECMAScript and
// WebAssembly and no way to reach the machine -- no process, no require, no
// module loader, no filesystem, no network.  That is not a policy this
// package applies, it is what an engine is without a host, and it is the
// reason to embed V8 rather than a JavaScript runtime.  If any of these names
// ever resolves, the property is gone and the test should say so.

#include <cstdio>
#include <cstdlib>
#include <initializer_list>
#include <memory>
#include <string>

#include "libplatform/libplatform.h"
#include "v8-context.h"
#include "v8-initialization.h"
#include "v8-isolate.h"
#include "v8-local-handle.h"
#include "v8-primitive.h"
#include "v8-script.h"

namespace {

int failures = 0;

// Runs one expression in the given context and returns it as a UTF-8 string.
std::string Eval(v8::Isolate* isolate, v8::Local<v8::Context> context,
                 const char* expression) {
  v8::EscapableHandleScope scope(isolate);
  v8::Local<v8::String> source =
      v8::String::NewFromUtf8(isolate, expression).ToLocalChecked();
  v8::Local<v8::Script> script =
      v8::Script::Compile(context, source).ToLocalChecked();
  v8::Local<v8::Value> result = script->Run(context).ToLocalChecked();
  v8::String::Utf8Value utf8(isolate, result);
  return std::string(*utf8, utf8.length());
}

void Check(const char* what, const std::string& got, const std::string& want) {
  const bool ok = got == want;
  std::printf("%-40s %-12s %s\n", what, got.c_str(), ok ? "ok" : "FAILED");
  if (!ok) {
    std::printf("    expected %s\n", want.c_str());
    ++failures;
  }
}

}  // namespace

int main(int argc, char* argv[]) {
  // The whole embedding ceremony.  It lives here, in the consumer, and not in
  // the package: what the package ships is the engine.
  std::unique_ptr<v8::Platform> platform = v8::platform::NewDefaultPlatform();
  v8::V8::InitializePlatform(platform.get());
  v8::V8::Initialize();

  v8::Isolate::CreateParams create_params;
  create_params.array_buffer_allocator =
      v8::ArrayBuffer::Allocator::NewDefaultAllocator();
  v8::Isolate* isolate = v8::Isolate::New(create_params);
  {
    v8::Isolate::Scope isolate_scope(isolate);
    v8::HandleScope handle_scope(isolate);
    // An empty global.  Nothing is added to it anywhere in this file.
    v8::Local<v8::Context> context = v8::Context::New(isolate);
    v8::Context::Scope context_scope(context);

    Check("1+2*3-4/5", Eval(isolate, context, "1+2*3-4/5"), "6.2");
    Check("JSON round trip",
          Eval(isolate, context, "JSON.stringify({a:[1,2,3]})"),
          "{\"a\":[1,2,3]}");
    // Pyodide and every other wasm guest needs this one to exist.
    Check("typeof WebAssembly", Eval(isolate, context, "typeof WebAssembly"),
          "object");

    // What a bare engine must not have.
    for (const char* name : {"process", "require", "module", "fs", "fetch",
                             "XMLHttpRequest", "read", "readFile", "quit",
                             "global", "Bun", "Deno"}) {
      char expression[64];
      std::snprintf(expression, sizeof(expression), "typeof %s", name);
      char label[80];
      std::snprintf(label, sizeof(label), "typeof %s", name);
      Check(label, Eval(isolate, context, expression), "undefined");
    }
    // The other half of the same claim: no dynamic loader to reach around the
    // missing globals with.  import() rejects rather than resolving, because
    // nothing installed a module callback.
    Check("import() without a host callback",
          Eval(isolate, context,
               "(() => { try { new Function('return import(\"fs\")')(); }"
               " catch (e) { return 'threw'; } return 'no throw'; })()"),
          "threw");
  }

  isolate->Dispose();
  v8::V8::Dispose();
  v8::V8::DisposePlatform();
  delete create_params.array_buffer_allocator;

  if (failures != 0) {
    std::printf("%d check(s) failed\n", failures);
    return 1;
  }
  std::printf("all checks passed\n");
  return 0;
}
