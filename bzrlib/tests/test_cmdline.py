from bzrlib import cmdline

class TestParser(tests.TestCase):

    def assertAsTokens(self, expected, line, single_quotes_allowed=False):
        s = cmdline.Parser(line, single_quotes_allowed=single_quotes_allowed)
        self.assertEqual(expected, list(s))

    def test_simple(self):
        self.assertAsTokens([(False, u'foo'), (False, u'bar'), (False, u'baz')],
                            u'foo bar baz')

    def test_ignore_multiple_spaces(self):
        self.assertAsTokens([(False, u'foo'), (False, u'bar')], u'foo  bar')

    def test_ignore_leading_space(self):
        self.assertAsTokens([(False, u'foo'), (False, u'bar')], u'  foo bar')

    def test_ignore_trailing_space(self):
        self.assertAsTokens([(False, u'foo'), (False, u'bar')], u'foo bar  ')

    def test_posix_quotations(self):
        self.assertAsTokens([(True, u'foo bar')], u"'foo bar'",
            single_quotes_allowed=True)
        self.assertAsTokens([(True, u'foo bar')], u"'fo''o b''ar'",
            single_quotes_allowed=True)
        self.assertAsTokens([(True, u'foo bar')], u'"fo""o b""ar"',
            single_quotes_allowed=True)
        self.assertAsTokens([(True, u'foo bar')], u'"fo"\'o b\'"ar"',
            single_quotes_allowed=True)

    def test_nested_quotations(self):
        self.assertAsTokens([(True, u'foo"" bar')], u"\"foo\\\"\\\" bar\"")
        self.assertAsTokens([(True, u'foo\'\' bar')], u"\"foo'' bar\"")
        self.assertAsTokens([(True, u'foo\'\' bar')], u"\"foo'' bar\"",
            single_quotes_allowed=True)
        self.assertAsTokens([(True, u'foo"" bar')], u"'foo\"\" bar'",
            single_quotes_allowed=True)

    def test_empty_result(self):
        self.assertAsTokens([], u'')
        self.assertAsTokens([], u'    ')

    def test_quoted_empty(self):
        self.assertAsTokens([(True, '')], u'""')
        self.assertAsTokens([(False, u"''")], u"''")
        self.assertAsTokens([(True, '')], u"''", single_quotes_allowed=True)

    def test_unicode_chars(self):
        self.assertAsTokens([(False, u'f\xb5\xee'), (False, u'\u1234\u3456')],
                             u'f\xb5\xee \u1234\u3456')

    def test_newline_in_quoted_section(self):
        self.assertAsTokens([(True, u'foo\nbar\nbaz\n')], u'"foo\nbar\nbaz\n"')
        self.assertAsTokens([(True, u'foo\nbar\nbaz\n')], u"'foo\nbar\nbaz\n'",
            single_quotes_allowed=True)

    def test_escape_chars(self):
        self.assertAsTokens([(False, u'foo\\bar')], u'foo\\bar')

    def test_escape_quote(self):
        self.assertAsTokens([(True, u'foo"bar')], u'"foo\\"bar"')
        self.assertAsTokens([(True, u'foo\\"bar')], u'"foo\\\\\\"bar"')
        self.assertAsTokens([(True, u'foo\\bar')], u'"foo\\\\"bar"')

    def test_double_escape(self):
        self.assertAsTokens([(True, u'foo\\\\bar')], u'"foo\\\\bar"')
        self.assertAsTokens([(False, u'foo\\\\bar')], u"foo\\\\bar")
        
    def test_multiple_quoted_args(self):
        self.assertAsTokens([(True, u'x x'), (True, u'y y')],
            u'"x x" "y y"')
        self.assertAsTokens([(True, u'x x'), (True, u'y y')],
            u'"x x" \'y y\'', single_quotes_allowed=True)
