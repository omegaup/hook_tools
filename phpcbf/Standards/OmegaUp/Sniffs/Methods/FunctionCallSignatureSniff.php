<?php

namespace PHP_CodeSniffer\Standards\OmegaUp\Sniffs\Methods;

class FunctionCallSignatureSniff extends \PHP_CodeSniffer\Standards\PSR2\Sniffs\Methods\FunctionCallSignatureSniff {
    /**
     * Determine if this is a multi-line function declaration.
     *
     * @param \PHP_CodeSniffer\Files\File $phpcsFile   The file being scanned.
     * @param int                         $stackPtr    The position of the
     *                                                 current token in the
     *                                                 stack passed in $tokens.
     * @param int                         $openBracket The position of the
     *                                                 opening bracket in the
     *                                                 stack passed in $tokens.
     * @param array                       $tokens      The stack of tokens that
     *                                                 make up the file.
     *
     * @return bool
     */
    public function isMultiLineCall(
        $phpcsFile,
        $stackPtr,
        $openBracket,
        $tokens
    ) {
        $closeBracket = $tokens[$openBracket]['parenthesis_closer'];
        $nextToken = $phpcsFile->findNext(
            T_WHITESPACE,
            ($openBracket + 1),
            null,
            true
        );
        if ($nextToken == $closeBracket) {
            // If there are no non-whitespace tokens between the open and close
            // parens, it is a single-line call.
            return false;
        }

        if (
            parent::isMultiLineCall(
                $phpcsFile,
                $stackPtr,
                $openBracket,
                $tokens
            )
        ) {
            return true;
        }

        $lastTokenInLine = $closeBracket;
        while (true) {
            $nextToken = $phpcsFile->findNext(
                T_WHITESPACE,
                ($lastTokenInLine + 1),
                null,
                true
            );
            if (
                $nextToken === false ||
                $tokens[$nextToken]['line'] != $tokens[$closeBracket]['line']
            ) {
                break;
            }
            $lastTokenInLine = $nextToken;
        }

        // If the parameter list causes this line to be too large, it needs to
        // be broken down in multiple lines.
        return $tokens[$lastTokenInLine]['column'] > 80;
    }
}
