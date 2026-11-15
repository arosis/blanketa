// Replays the script runs tools/compare_js_engines.py recorded in zfoengine/js602.py and prints what each gave in V8.
// The scripts are untrusted: the container this runs in has no network and nothing to write to.
const fs = require("fs");
const vm = require("vm");

const TIME_FOR_ONE_RUN = 5000;
const input = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));

const HOST_AS_BLANKETA_GIVES_IT = `(function (now, answerTo, giveResult, currentPath) {
  var RealDate = Date;
  function FrozenDate(a, b, c, d, e, f, g) {
    if (!(this instanceof FrozenDate)) return new RealDate(now).toString();
    if (arguments.length === 0) return new RealDate(now);
    if (arguments.length === 1) return new RealDate(a);
    return new RealDate(a, b, c === undefined ? 1 : c, d || 0, e || 0, f || 0, g || 0);
  }
  FrozenDate.prototype = RealDate.prototype;
  FrozenDate.now = function () { return now; };
  FrozenDate.parse = RealDate.parse;
  FrozenDate.UTC = RealDate.UTC;
  Date = FrozenDate;
  Math.random = function () { return 0.5; };

  var element = {
    DataXpath: function (expression) { return answerTo("DataXpath", String(expression)); },
    GetCurrentDataXPath: function () { return currentPath(); },
    GetListItemData: function () { return answerTo("GetListItemData", Array.prototype.map.call(arguments, String).join("\\0")); }
  };
  var form = { GetElement: function () { return element; }, SetProgressInfo: function () {} };
  GetDocument = function () { return form; };
  DbgLog = function () {};
  RCCheck = function (text) { return answerTo("RCCheck", String(text)); };
  Result = function (text) {
    if (arguments.length !== 1 || typeof text !== "string") throw new TypeError("Result expects one text");
    giveResult(text);
  };
})`;

function outcomeOf(error) {
  if (error && error.code === "ERR_SCRIPT_EXECUTION_TIMEOUT") return ["timeout"];
  if (error && error.notAnswered) return ["asked what js602 did not", error.notAnswered];
  if (String(error).indexOf("Maximum call stack") >= 0) return ["exhausted"];
  return ["thrown", String(error)];
}

function replayed(form) {
  const context = vm.createContext({});
  let run = null;
  let answers = null;
  let result = null;

  function answerTo(kind, question) {
    const waiting = answers[kind + "\0" + question];
    if (!waiting || waiting.length === 0) {
      const error = new Error("not answered");
      error.notAnswered = kind + " " + question;
      throw error;
    }
    return waiting.length > 1 ? waiting.shift() : waiting[0];
  }

  vm.runInContext(HOST_AS_BLANKETA_GIVES_IT, context)(input.now, answerTo, function (text) { result = text; }, function () { return run.path; });
  for (const source of form.library) {
    try {
      vm.runInContext(source, context, { timeout: TIME_FOR_ONE_RUN });
    } catch (error) {
      continue;
    }
  }

  return form.runs.map(function (recorded) {
    run = recorded;
    result = null;
    answers = {};
    for (const [kind, question, answer] of recorded.asked) {
      (answers[kind + "\0" + question] = answers[kind + "\0" + question] || []).push(answer);
    }
    try {
      vm.runInContext("(function () {" + recorded.source + "\n})()", context, { timeout: TIME_FOR_ONE_RUN });
    } catch (error) {
      return outcomeOf(error);
    }
    return result === null ? ["nothing"] : ["result", result];
  });
}

process.stdout.write(JSON.stringify(replayed(input.form)));
