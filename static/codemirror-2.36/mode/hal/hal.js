CodeMirror.defineMode('hal', function(config) {

    var words = {};
    function define(style, string) {
        var split = string.split(' ');
        for(var i = 0; i < split.length; i++) {
            words[split[i]] = style;
        }
    };

    // Atoms
    define('atom', '');

    // Keywords
    define('keyword', 'bit s32 u32 float comp pin sig param funct thread all sigu link linka net neta');

    // Commands
    define('builtin', 'addf delf delsig getp linkps linkpp linksp loadrt loadusr net newsig ptype save setp setp sets show source start stop stype unlinkp unload unloadrt unloadusr waitusr');
    
    // atribute
    define('attribute', '-W -Wn -w -i')
    
    function tokenBase(stream, state) {

        var sol = stream.sol();
        var ch = stream.next();

        if (ch === '\'' || ch === '"' || ch === '`' ) {
            state.tokens.unshift(tokenString(ch));
            return tokenize(stream, state);
        }
        if (ch === '[' ) {
            state.tokens.unshift(tokenString(']'));
            return tokenize(stream, state);
        }

        if (ch === '#') {
            if (sol && stream.eat('!')) {
                stream.skipToEnd();
                return 'meta'; // 'comment'?
            }
            stream.skipToEnd();
            return 'comment';
        }
        if (ch === '$') {
            state.tokens.unshift(tokenDollar);
            return tokenize(stream, state);
        }
        if (ch === '+' || (ch === '=') || (ch === '<') || (ch === '>') )  {
            return 'operator';
        }

        if (/\d/.test(ch)) {
            stream.eatWhile(/[\d\.]/);
            if(stream.eol() || !/\w/.test(stream.peek())) {
                return 'number';
            }
        }
        stream.eatWhile(/[\w\.]/);
        var cur = stream.current();
        if (stream.peek() === '=' && /\w+/.test(cur) && !stream.match('=>', false, false )) return 'def';

        return words.hasOwnProperty(cur) ? words[cur] : null;
    }

    function tokenString(quote) {
        return function(stream, state) {
            var next, end = false, escaped = false;
            while ((next = stream.next()) != null) {
                if (next === quote && !escaped) {
                    end = true;
                    break;
                }
                if (next === '$' && !escaped && quote !== '\'') {
                    escaped = true;
                    stream.backUp(1);
                    state.tokens.unshift(tokenDollar);
                    break;
                }
                escaped = !escaped && next === '\\';
            }
            if (end || !escaped) {
                state.tokens.shift();
            }
            return 'string';
            //return (quote === '`' || quote === ')' ? 'quote' : 'string');
        };
    };

    var tokenDollar = function(stream, state) {
        if (state.tokens.length > 1) stream.eat('$');
        var ch = stream.next(), hungry = /[\w]/;
        if (ch === '{') hungry = /[^}]/;
        if (ch === '(') hungry = /[^)]/;
//        if (ch === '(') {
//            state.tokens[0] = tokenString(')');
//            return tokenize(stream, state);
//        }
        if (!/\d/.test(ch)) {
            stream.eatWhile(hungry);
            stream.eat('}');
            stream.eat(')');
        }
        state.tokens.shift();
        return 'def';
    };

    function tokenize(stream, state) {
        return (state.tokens[0] || tokenBase) (stream, state);
    };

    return {
        startState: function() {
            return {
                tokens:[]
            };
        
        },
        token: function(stream, state) {
            if (stream.eatSpace()) return null;
            return tokenize(stream, state);
        }
    };
});
  
CodeMirror.defineMIME('text/x-sh', 'shell');
