import os, threading
from collections import defaultdict
from ..context import context
from mako.lookup import TemplateLookup
from mako.parsetree import Tag, Text
from mako import ast

__all__ = ['make_function']

loaded = {}
lookup = None

class NestedTemplateNewlineHandler(threading.local):
    """
    When shellcode templates are invoked directly, we want them
    to be terminated with a newline.

    When shellcode templates are invoked by other shellcode templates,
    we don't want this behavior to occur.  This is for several reasons,
    including keeping assembly code compact, and permitting the current
    use of labels as ``${label}:``.
    """
    nest = 0
    def __enter__(self):
        append = '' if nest else '\n'
        self.nest += 1
        return append
    def __exit__(self, *a):
        self.nest -= 1

nester = NestedTemplateNewlineHandler

class pwn_docstring(Tag):
    """
    Defines a new tag which Mako will interpret for docstrings.
    This allows us to put <%docstring>Lorem ipsum</%docstring> inside
    of the assembly templates.
    """
    __keyword__ = 'docstring'

    def __init__(self, *args, **kwargs):
        super(pwn_docstring, self).__init__('docstring', (), (), (), (), **kwargs)
        self.ismodule = True

    @property
    def text(self):
        children = self.get_children()
        if len(children) != 1 or not isinstance(children[0], Text):
            raise Exception("docstring tag only supports text")

        docstring = children[0].content

        return '__doc__ = %r' % docstring

    @property
    def code(self):
        return ast.PythonCode(self.text)

    def accept_visitor(self, visitor):
        method = getattr(visitor, "visitCode", lambda x: x)
        method(self)


def lookup_template(filename):
    curdir = os.path.dirname(os.path.abspath(__file__))
    lookup = TemplateLookup(
        directories      = [os.path.join(curdir, 'templates')],
        module_directory = context.cache
    )
    return lookup.get_template(filename)

def make_function(funcname, filename, directory):
    import inspect
    path       = os.path.join(directory, filename)
    template   = lookup_template(path)

    args, varargs, keywords, defaults = inspect.getargspec(template.module.render_body)

    defaults = defaults or []

    if len(defaults) < len(args) and args[0] == 'context':
        args.pop(0)

    args_used = args[:]

    for n, default in enumerate(defaults, len(args) - len(defaults)):
        args[n] = '%s = %r' % (args[n], default)

    if varargs:
        args.append('*' + varargs)
        args_used.append('*' + varargs)

    if keywords not in ['pageargs', None]:
        args.append('**' + keywords)
        args_used.append('**' + keywords)

    args      = ', '.join(args)
    args_used = ', '.join(args_used)

    # This is a slight hack to get the right signature for the function
    # It would be possible to simply create an (*args, **kwargs) wrapper,
    # but what would not have the right signature.
    # While we are at it, we insert the docstring too
    exec '''
def wrap(template, render_global):
    def %s(%s):
        %r
        with context.local(os=%r, arch=%r):
            with render_global.go_inside() as was_inside:
                lines = template.render(%s).split('\\n')
        for i in xrange(len(lines)):
            line = lines[i]
            def islabelchar(c):
                return c.isalnum() or c == '.' or c == '_'
            if ':' in line and islabelchar(line.lstrip()[0]):
                line = line.lstrip()
            elif line.startswith(' '):
                 line = '    ' + line.lstrip()
            lines[i] = line
        while lines and not lines[-1]: lines.pop()
        while lines and not lines[0]:  lines.pop(0)
        s = '\\n'.join(lines)
        while '\\n\\n\\n' in s:
            s = s.replace('\\n\\n\\n', '\\n\\n')

        if was_inside:
            return s
        else:
            return s + '\\n'
    return %s
''' % (funcname, args, inspect.cleandoc(template.module.__doc__ or ''), args_used, funcname)

    # Setting _relpath is a slight hack only used to get better documentation
    res = wrap(template, render_global)
    res._relpath = path

    return res
