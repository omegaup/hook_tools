<?php

class CastSniff implements PHP_CodeSniffer\Sniffs\Sniff {
    /**
     * Returns an array of tokens this test wants to listen for.
     *
     * @return array
     */
    public function register() {
        return PHP_CodeSniffer\Util\Tokens::$castTokens;
    }

    /**
     * Processes this test, when one of its tokens is encountered.
     *
     * @param \PHP_CodeSniffer\Files\File $phpcsFile The file being scanned.
     * @param int                         $stackPtr  The position of the current token in
     *                                               the stack passed in $tokens.
     *
     * @return void
     */
    public function process(PHP_CodeSniffer\Files\File $phpcsFile, $stackPtr) {
        $tokens    = $phpcsFile->getTokens();
        $errorData = [strtolower($tokens[$stackPtr]['content'])];

        if ($tokens[$stackPtr]['code'] === T_BINARY_CAST
            && $tokens[$stackPtr]['content'] === 'b'
        ) {
            // You can't replace a space after this type of binary casting.
            return;
        }
        if ($tokens[$stackPtr]['code'] === T_ARRAY_CAST
            || $tokens[$stackPtr]['code'] === T_OBJECT_CAST
        ) {
            // These two don't have a function equivalent.
            return;
        }
        $phpcsFile->addError(
            'Disallowed cast: %s. Use intval()/floatval()/boolval()/strval() instead',
            $stackPtr,
            'DisallowedCast',
            $errorData
        );
    }
}
