# Bash tab completion rules for the 'upyt' CLI.
#
# Source this file to enable completion.
#
# Warning: When tab-completing filenames, the attached MicroPython device will
# have its current execution interrupted to enumerate its filesystem. If you
# wish to disable this behaviour (along with all filename completion), set the
# `UPYT_NO_COMPLETE_PATHS` environment variable to any non-empty value.


# Filter the output of --help to just list all of the command line options
# Argument 1: the upyt command name
# Argument 2: the upyt subcommand used (or empty string for the top-level)
_upyt_help_to_options() {
    CMD="$1"
    SUB_CMD="$2"
    "$CMD" $SUB_CMD --help | awk '
        BEGIN {
            relevant = 0
        }
        /^options:$/ {
            relevant = 1
        }
        /^  -.*/ {
            if (relevant) {
                for (i = 1; i <= NF; i++) {
                    if ($i ~ /^--?[-a-zA-Z0-9]+,?$/) {
                        gsub(",", "", $i)
                        print $i " "
                    }
                }
            }
        }
    '
}

# Filter the output of --help to just list all of the subcommands
# Argument 1: the upyt command name
_upyt_help_to_subcommands() {
    CMD="$1"
    "$CMD" $SUB_CMD --help | awk '
        BEGIN {
            relevant = 0
        }
        /^commands:$/ {
            relevant = 1
        }
        match($0, /^  \{(.*)\}$/, arg) {
            if (relevant) {
                split(arg[1], subcommands, ",")
                for (i in subcommands) {
                    print subcommands[i] " "
                }
            }
        }
    '
}

_upyt() {
    CMD="${COMP_WORDS[0]}"
    CUR="${COMP_WORDS[COMP_CWORD]}"
    PREV="${COMP_WORDS[COMP_CWORD-1]}"
    
    # Determine the subcommand in use (if any)
    SUBCOMMAND=""
    SUBCOMMAND_INDEX=0
    for i in `seq 1 $(($COMP_CWORD - 1))`; do
        if [[ ${COMP_WORDS[i-1]} != --device ]] && \
           [[ ${COMP_WORDS[i-1]} != -d ]] &&
           [[ ${COMP_WORDS[i-1]} != = ]] &&
           [[ ${COMP_WORDS[i]} != -* ]]; then
           SUBCOMMAND="${COMP_WORDS[i]}"
           SUBCOMMAND_INDEX=$i
           break
        fi
    done
    
    # Auto-complete subcommands
    if [[ $CUR != -* ]] && [[ -z $SUBCOMMAND ]]; then
        IFS=$'\n' COMPREPLY=($(compgen -W "$(_upyt_help_to_subcommands "$CMD")" -- "$CUR"))
        return 0
    fi
    
    # Auto-complete options (e.g. -f --foo)
    if [[ $CUR == -* ]]; then
        IFS=$'\n' COMPREPLY=($(compgen -W "$(_upyt_help_to_options "$CMD" "$SUBCOMMAND")" -- "$CUR"))
        return 0
    fi
    
    # Determine the device name
    if [[ -n $SUBCOMMAND ]]; then
        # Default to environment variable
        DEVICE="$UPYT_DEVICE"
        
        # ...but scan for a -d/--device argument
        for i in `seq 1 $(($SUBCOMMAND_INDEX - 1))`; do
            # --device=foo case
            if [[ ${COMP_WORDS[i-2]} == @(-d|--device) ]] && \
               [[ ${COMP_WORDS[i-1]} == = ]]; then
              DEVICE="${COMP_WORDS[i]}"
              break
            fi
            # --device foo case
            if [[ ${COMP_WORDS[i-1]} == @(-d|--device) ]] && \
               [[ ${COMP_WORDS[i]} != = ]]; then
              DEVICE="${COMP_WORDS[i]}"
              break
            fi
        done
    fi
    
    # Auto-complete all other arguments as filenames on the host and device.
    #
    # Since performing an 'ls' operation on a device will kill any running
    # process, the UPYT_NO_COMPLETE_PATHS variable may be used to disable this
    # feature.
    if [[ -z $UPYT_NO_COMPLETE_PATHS ]] && [[ -n $SUBCOMMAND ]]; then
        # Strip off any partially complete filenames from path so we can feed
        # it to 'ls'
        DIR="$(echo "$CUR" | sed -nre 's:^(([^/\\]*[/\\])*)[^/\\]*$:\1:p')"
        
        # We will need to prefix the path above with ':' (for device paths) or
        # './' (for relative host paths) or nothing (for absolute host paths)
        # to ensure that the upyt ls command looks in the right place.
        #
        # Due to the fact that readline treats ':' as a word separator (see
        # COMP_WORDBREAKS), the colon in a device path will end up in the
        # previous word token. This is why we end up having the complicated
        # series of tests below.
        #
        # Secondly, if tab completing on a bare ':', we must also temporarily
        # prefix all of the completions with ':' for the benefit of compgen --
        # and then remove these from our offered completion strings. Gross huh?
        PREFIX=""
        COMPGENPREFIX=""
        if [[ $PREV == : ]]; then
            PREFIX=:
        elif [[ $CUR == : ]]; then
            PREFIX=:
            COMPGENPREFIX=:
        elif [[ $PREV != : ]] && [[ $CUR != /* ]]; then
            PREFIX=./
        fi
        
        # Enumerate the possible completions.
        if [[ -z $CUR ]]; then
            # Special case: if no path has been entered yet, complete to both
            # device and host paths!
            IFS=$'\n' CANDIDATES=($(
                $CMD --device=$DEVICE ls : 2>/dev/null | sed -e s/^/:/
                $CMD --device=$DEVICE ls ./ 2>/dev/null
            ))
        else
            IFS=$'\n' CANDIDATES=($($CMD --device=$DEVICE ls "$PREFIX$DIR" 2>/dev/null))
        fi
        
        # Prefix with directory name
        CANDIDATES=("${CANDIDATES[@]/#/$COMPGENPREFIX$DIR}")
        
        # Filter to only matching candidates
        IFS=$'\n' CANDIDATES=($(compgen -W "${CANDIDATES[*]}" -- "$CUR"))
        
        # Remove temporary prefix added for the benefit of compgen.
        CANDIDATES=("${CANDIDATES[@]/#$COMPGENPREFIX/}")
        
        # Set COMPREPLY with escaped versions of all completions
        if [[ ${#CANDIDATES[@]} -gt 0 ]]; then
            IFS=$'\n' COMPREPLY=($(printf "%q\n" "${CANDIDATES[@]}"))
        fi
        return
    fi
}

complete -o nospace -F _upyt upyt
